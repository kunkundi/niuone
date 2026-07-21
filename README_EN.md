<img width="2115" height="744" alt="niuone" src="https://github.com/user-attachments/assets/50dd932a-5af9-441a-b17a-d63a0b6801ac" />

# NiuOne · 牛牛1号

[简体中文](README.md) | English

<p align="left">
  <a href="https://linux.do"><img src="https://shorturl.at/ggSqS" alt="LINUX DO" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License" /></a>
  <a href="https://github.com/kunkundi/niuone/actions/workflows/ci.yml"><img src="https://github.com/kunkundi/niuone/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://hub.docker.com/r/kunkundi/niuone"><img src="https://img.shields.io/docker/pulls/kunkundi/niuone?label=Docker%20Pulls" alt="Docker Pulls" /></a>
</p>

## Introduction

NiuOne is a research and simulated trading system for China's A-share market. LLMs drive market analysis and trading decisions, while the system provides market data aggregation, news retrieval, strategy configuration, and simulated portfolio tracking.

The web dashboard provides a unified view of market data, analysis results, and simulated portfolio status. Scheduled jobs automatically collect pre-open call auction data, intraday and post-market activity, capital flows, sector performance, overnight U.S. market activity, institutional ratings, and content from a Twitter/X watchlist. Guided by user-configured strategy rules, LLMs perform news retrieval, market analysis, and simulated buy and sell decisions. Portfolio state, trade records, and decision rationale are stored locally, while simulated execution alerts can be delivered through Feishu, DingTalk, WeCom, or Telegram.

NiuOne connects information collection, market analysis, trading decisions, and portfolio records into an automated, traceable, and reviewable simulated trading loop. The system can be deployed on a personal computer or server, with configuration and research data managed by the user. All trades are executed in a simulated account; NiuOne does not connect to brokerage trading interfaces or use real funds.

## Live Demo

<https://niuone.cn>

> This page is intended solely for personal research, simulated trading, and information display. It does not constitute securities or futures investment consulting, investment advice, stock recommendations, or any basis for buying or selling. No returns are promised; no assets are managed on behalf of others; and no fees are charged for stock recommendations.

## Feature Overview

- **Unified dashboard**: View indices, sectors, market sentiment, capital flows, and historical news in one place.
- **Information aggregation**: Organize A-share market data, U.S. market summaries, institutional ratings, and content from a Twitter/X watchlist.
- **Intelligent summaries**: Connect compatible large-model services to summarize and structure information from multiple sources.
- **Custom trading strategies**: Choose a built-in strategy or describe your own candidate-selection, buy, sell, position-sizing, and timing rules in natural language.
- **Simulated trading and portfolio tracking**: Use your own simulated account for candidate screening, buy and sell decisions, position and P&L tracking, and access to the equity curve and trading logs—all without connecting to a brokerage or using real funds.
- **Automated tasks**: Schedule data collection, summary generation, database ingestion, and background monitoring.
- **User-controlled data**: Configuration, databases, logs, and task output are stored in a separate runtime directory by default. They remain under the user's control and are not committed with the source code.

The main README does not cover specific research methods or experimental strategies in detail. See the [Strategy Research Notes](docs/strategies/README_EN.md).

When contributing or extending the application, see the [app module architecture](docs/APP_ARCHITECTURE.md) for domain boundaries and compatibility-entrypoint conventions.

The Dashboard has migrated to Vue 3 + Vite and FastAPI/Uvicorn while preserving its existing page layout. Same-origin incremental snapshots reduce public traffic, and trading, market requests, and record computation remain server-side. The public page, `/admin`, and every API share one production port. See [Dashboard Incremental Delivery and Deployment](docs/DASHBOARD_V2_EN.md) for architecture, caching, and CDN/cloud/Tunnel deployment guidance.

## System Requirements

| Dependency | Requirement | Purpose |
|---|---|---|
| Python | 3.11+ | Run services, task scripts, and local tools |
| Node.js | 22.12+ | Build the Vue 3/Vite frontend; not needed in the runtime container image |
| pnpm | 11.15.1 (the launcher may invoke it through npx) | Install locked frontend dependencies and build the app |
| Git | Latest stable release recommended | Download and update the project |
| Browser | A modern browser such as Chrome, Edge, Safari, or Firefox | Access the local workspace |
| Network | PyPI and npm registry access are required on the first run | Install Python and frontend dependencies |

## Quick Start

Clone the project:

```bash
git clone https://github.com/kunkundi/niuone.git
cd niuone
```

macOS / Linux:

```bash
./run.sh
```

If Linux reports that the script is not executable:

```bash
chmod +x run.sh
./run.sh
```

On Windows, double-click `run.bat`, or run it from CMD:

```cmd
run.bat
```

After startup completes, open:

```text
http://127.0.0.1:8787/
```

On the first run, NiuOne automatically:

1. Creates the private `.local-data/` runtime directory;
2. Creates a Python virtual environment at `.local-data/.venv/`;
3. Installs the dependencies in `requirements.txt`;
4. Installs and builds the Vue frontend from `web/pnpm-lock.yaml`;
5. Generates `.local-data/dashboard.env`;
6. Initializes the runtime directory and starts the FastAPI dashboard.

### Common Startup Options

| Option | Description |
|---|---|
| `--port VALUE` | Set and save the dashboard port |
| `--no-browser` | Do not open a browser automatically after startup |
| `--skip-install` | Skip the dependency installation check |
| `--service` | Register and start a long-running service for the current platform |

For example, to use port `8877` without opening a browser automatically:

```bash
./run.sh --port 8877 --no-browser
```

Windows:

```cmd
run.bat --port 8877 --no-browser
```

The dashboard home page and display data remain publicly accessible, while the settings page and management APIs always require administrator authentication. On the first startup, use the bootstrap administrator key generated by the service to access the settings page. Its local path is `$DASHBOARD_HOME/dashboard_admin_token.txt`, which defaults to `.local-data/runtime/dashboard_admin_token.txt`. After signing in, you can set an administrator password under “Access Control”; the new password takes effect immediately and signs out existing sessions. Alternatively, before startup you can edit `.local-data/dashboard.env`, whose permissions are set to `0600`, and set `DASHBOARD_ADMIN_PASSWORD` directly. Do not pass passwords through command-line arguments, as they may be recorded in shell history or exposed in the process list.

To store runtime data somewhere else, set:

```bash
NIUONE_LOCAL_DATA_DIR=/path/to/private-data ./run.sh
```

## Container Deployment

The project provides a single image and a Compose setup. Compose starts the dashboard, scheduled-task runner, and X followed-source daemon, and persists configuration, databases, logs, and task output in the shared `niuone-data` volume.

Build and start from source:

```bash
docker compose up -d --build
docker compose ps
```

By default, the service is available at `127.0.0.1:8787`; the public page and password-protected `/admin` page share that port. To view logs or stop the service:

```bash
docker compose logs -f
docker compose down
```

Deploy a specific version from Docker Hub:

```bash
export NIUONE_IMAGE=kunkundi/niuone:v0.0.1
docker compose pull
docker compose up -d --no-build
```

Set `NIUONE_PORT` to change the host port. Change the bind address to `0.0.0.0` only after configuring a reverse proxy, HTTPS, and independent access control:

```bash
NIUONE_BIND_ADDRESS=0.0.0.0 NIUONE_PORT=8877 docker compose up -d
```

> The dashboard home page remains publicly accessible, while the settings page and management APIs always require administrator authentication. Containers use the `DASHBOARD_ADMIN_PASSWORD` configured in `/data/dashboard.env`. If it is not configured, run `docker compose exec dashboard cat /data/runtime/dashboard_admin_token.txt` to read the bootstrap administrator key. Runtime configuration and keys are stored in the volume and are not included in the image.

## Initial Configuration

The basic pages can start without a model key. Information retrieval, intelligent summaries, and some automated workflows require additional external services.

After startup, use the settings entry in the page to configure NiuOne. First authenticate with the configured administrator password or the local bootstrap administrator key. Configuration is written to the local `.local-data/` directory, so there is no need to modify the source code. For first-time setup, we recommend completing the following steps in order:

1. Select the data sources and automated tasks to enable;
2. Configure a compatible model service URL, model name, and API key as needed;
3. To receive trade alerts, turn on the master switch under “Trade Notifications,” then add the required channels from the drop-down list and enter their configuration. Telegram requires a Bot Token and Chat ID;
4. Store or rotate administrator credentials securely;
5. Restart the service so that all settings requiring a restart take effect.

### Trade Notification Configuration

NiuOne can send simulated buy and sell execution alerts to Feishu, DingTalk, WeCom, and Telegram. Notifications are sent only after a successful execution has been persisted. Multiple executions from the same cycle are combined into a single message and explicitly labeled “模拟成交，非实盘” (“simulated execution, not live trading”). A failure on one channel does not roll back the execution or affect other channels.

#### Adding a Channel on the Settings Page

1. Go to “Settings → Trade Notifications” and set “Enable simulated trade notifications” to “Enabled.”
2. “Timeout per notification in seconds” defaults to `5` seconds and can be set from `1` to `30` seconds.
3. Select a channel from the “Notification channels” drop-down list and click “Add channel.”
4. Complete the required fields on that channel's card, enter a signing secret if needed, and click “Send test notification” at the bottom of the card to validate the configuration.
5. After the test succeeds, click “Save this section’s settings” (`保存本组设置`) and add other channels as needed. Notification settings take effect immediately after saving; there is no need to restart the service specifically for notification changes.

Clicking “Remove” in the upper-right corner of a channel card first disables and collapses that channel. After you click “Save this section’s settings” (`保存本组设置`), NiuOne deletes the saved Webhook, Bot Token, Chat ID, and signing secret for that channel. If you add the channel again later, its status will be “Not set.” If you add the channel again before saving, the original configuration is not deleted. For a channel that remains added, leaving a sensitive field blank when saving preserves its existing value.

“Send test notification” sends only to the channel represented by the current card. It is unaffected by the master notification switch or the channel switch, and it does not save or modify configuration. The test uses unsaved values currently entered in the card first. If a sensitive field is blank, it falls back to the saved Webhook, Bot Token, or signing secret, while the Telegram Chat ID and timeout are validated using the current input. The test message includes “模拟成交，非实盘,” but it does not create an execution record or change cash or positions.

| Channel | Required configuration | Optional configuration | Targets accepted by NiuOne | Setup |
|---|---|---|---|---|
| Feishu | Bot Webhook | Signing secret | `https://open.feishu.cn/open-apis/bot/v2/hook/...` or `https://open.larksuite.com/open-apis/bot/v2/hook/...` | [View setup](#feishu) |
| DingTalk | Bot Webhook | Signing secret | `https://oapi.dingtalk.com/robot/send?access_token=...` | [View setup](#dingtalk) |
| WeCom | Bot Webhook | None | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...` | [View setup](#wecom) |
| Telegram | Bot Token, Chat ID | None | NiuOne calls the official `api.telegram.org` Bot API using the Token | [View setup](#telegram) |

#### Feishu

1. Open the target group chat and go to “Settings → Group Bots → Add Bot → Custom Bot.” Entry names may vary slightly between client versions.
2. After creating the bot, copy the complete Webhook into NiuOne's “Feishu Bot Webhook” field. Do not copy only the token in the path.
3. If “Signature Verification” is enabled in the Feishu bot's security settings, copy the original secret shown on that page into “Feishu Signing Secret (optional).” NiuOne automatically adds a timestamp in seconds and the signature. Do not enter a computed, temporary signature. “Optional” means the field can remain blank when signing is not enabled on Feishu; once signing is enabled there, this field is required.
4. If “Custom Keywords” is enabled, we recommend adding `模拟成交` so that execution notifications pass the keyword check. Keywords are configured only on the Feishu bot side.
5. If an IP allowlist is enabled, allow the public egress IP of the machine running NiuOne. The local address `127.0.0.1` is not the egress IP.

A Feishu custom bot belongs only to the group chat in which it was created. Feishu's current official limits are `100` requests per minute and `5` requests per second for each bot in a tenant. A Webhook is a sensitive credential; if it is leaked, others can send messages to the corresponding group chat. Do not commit real Webhooks to Git or include them in issues, logs, or screenshots. For detailed creation steps, security settings, and error codes, see the [Feishu Custom Bot Guide](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot?lang=en-US).

#### DingTalk

1. Open bot management in the target group chat and create a “Custom Bot.”
2. Follow DingTalk's prompts to configure a security method, then copy the complete Webhook into NiuOne's “DingTalk Bot Webhook” field. Use the `oapi.dingtalk.com/robot/send` URL containing `access_token`; application-bot and other OpenAPI URLs cannot be entered here directly.
3. If you select the “Signature” security method, enter the original `SEC...` secret shown in DingTalk's security settings into “DingTalk Signing Secret (optional).” NiuOne automatically generates the millisecond timestamp and URL-encoded signature parameters. Do not paste a temporary URL containing `timestamp` and `sign`, and do not enter a computed signature. “Optional” means the field can remain blank when signing is not enabled on DingTalk.
4. If keyword security is also enabled, we recommend configuring `模拟成交` as a keyword. Keywords are configured only on the DingTalk bot side.
5. If IP range security is used, allow the public egress IPv4 address or CIDR range of the machine running NiuOne.

The signing secret must match the current bot exactly. If you reset the bot's security settings, update the secret in NiuOne as well. DingTalk's current official limit is `20` messages per bot per minute; exceeding it may trigger rate limiting. See [Create a Custom Bot](https://open.dingtalk.com/document/dingstart/custom-bot-creation-and-installation), [Security Settings](https://open.dingtalk.com/document/dingstart/customize-robot-security-settings), [Obtain the Webhook](https://open.dingtalk.com/document/dingstart/obtain-the-webhook-address-of-a-custom-robot), and [Send Group Messages and Error Codes](https://open.dingtalk.com/document/development/custom-robots-send-group-messages).

#### WeCom

1. In WeCom, create “Message Push” (formerly “Group Bot”) for the target group chat. Entry names may vary between client versions; refer to the official “Message Push” page for the current interface.
2. Copy the complete, unique Webhook for that push from the “Create Message Push,” “Creation Complete,” or message-push details page into NiuOne's “WeCom Bot Webhook” field.
3. The Webhook must contain a unique, non-empty `key` parameter, and the message push must still belong to the target group chat that receives notifications. Do not enter only the `key`, and do not append other query parameters.

The WeCom channel does not require a separate signing field; the Webhook itself is the credential. NiuOne limits the notification body to `1900` bytes, below WeCom's `2048`-byte limit for text messages. If executions occur frequently within a short period, also be mindful of the platform's message-rate limits. If you delete the message push or regenerate its Webhook, replace the old URL in NiuOne. For the API format, see WeCom's [Message Push Configuration Guide](https://developer.work.weixin.qq.com/document/path/91770).

#### Telegram

1. Open the official [@BotFather](https://t.me/BotFather) in Telegram, run `/newbot`, follow the prompts to create a bot, and save the Bot Token.
2. To receive messages in a private chat, open the new bot and send `/start` first, because a bot cannot initiate a private conversation with a user who has not started one.
3. To receive messages in a group, add the bot to the target group and send a command explicitly addressed to it, such as `/start@bot_username`. Under the default Privacy Mode, regular group messages may not appear in the bot's update list. To receive messages in a channel, make the bot an administrator with permission to post messages.
4. You can first call the official `getMe` method to confirm that the Token is valid.
5. To obtain the Chat ID, send a new message to the target conversation and call the official `getUpdates` method. For private chats and groups, it is usually found at `result[].message.chat.id`; for channels, at `result[].channel_post.chat.id`; and membership status updates may also expose it at `result[].my_chat_member.chat.id`. Group and channel IDs are usually negative. Copy the entire value and do not add or remove the `-100` prefix yourself.
6. Enter the Token provided by BotFather in “Telegram Bot Token.” Enter only the Token itself in a form such as `123456:ABC...`; do not add the `bot` prefix or paste the full API URL. Enter the numeric Chat ID in “Telegram Chat ID.” For a public supergroup or channel, you may also enter `@channel_username`; for a private chat, a regular `@username` cannot replace the numeric Chat ID.

If `getUpdates` returns an empty array, first confirm that the target conversation received a new message after the bot joined. If a Webhook has already been configured for the bot to receive updates, `getUpdates` is unavailable. NiuOne only sends notifications and does not configure Telegram's incoming Webhook. Current notifications do not set `message_thread_id`, so they cannot target a specific Topic in a forum group. The Bot Token is equivalent to control credentials for the bot; revoke or regenerate it through BotFather immediately if it is leaked. See Telegram's official [Bot Creation Guide](https://core.telegram.org/bots), [`getMe` documentation](https://core.telegram.org/bots/api#getme), [`getUpdates` documentation](https://core.telegram.org/bots/api#getupdates), and [`sendMessage` documentation](https://core.telegram.org/bots/api#sendmessage).

#### Settings and Environment Variables

The settings page writes configuration to the private `.local-data/dashboard.env`. For manual configuration, see [dashboard.env.example](dashboard.env.example). Whether a channel is added or removed is represented by its corresponding `*_NOTIFICATION_ENABLED` switch.

| Purpose | Environment variable | Default |
|---|---|---|
| Master notification switch | `DASHBOARD_NOTIFICATION_ENABLED` | `0` |
| Per-channel request timeout | `DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS` | `5` |
| Feishu channel switch | `DASHBOARD_FEISHU_NOTIFICATION_ENABLED` | `0` |
| Feishu Webhook | `DASHBOARD_FEISHU_WEBHOOK_URL` | Empty |
| Feishu signing secret | `DASHBOARD_FEISHU_SIGNING_SECRET` | Empty |
| DingTalk channel switch | `DASHBOARD_DINGTALK_NOTIFICATION_ENABLED` | `0` |
| DingTalk Webhook | `DASHBOARD_DINGTALK_WEBHOOK_URL` | Empty |
| DingTalk signing secret | `DASHBOARD_DINGTALK_SIGNING_SECRET` | Empty |
| WeCom channel switch | `DASHBOARD_WECOM_NOTIFICATION_ENABLED` | `0` |
| WeCom Webhook | `DASHBOARD_WECOM_WEBHOOK_URL` | Empty |
| Telegram channel switch | `DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED` | `0` |
| Telegram Bot Token | `DASHBOARD_TELEGRAM_BOT_TOKEN` | Empty |
| Telegram Chat ID | `DASHBOARD_TELEGRAM_CHAT_ID` | Empty |

#### Troubleshooting

| Symptom | What to check |
|---|---|
| No messages arrive on any channel | Confirm that the master notification switch is enabled, at least one channel has been added and saved, and a simulated execution was successfully persisted. |
| Only one channel fails | Check that the corresponding channel card is still in the added state and that the Webhook, Token, and Chat ID belong to the same bot and target conversation. |
| Feishu returns `19024`, or DingTalk reports a keyword mismatch | Add `模拟成交` in the bot's security settings, or adjust its keyword rules. |
| Feishu returns `19021`, DingTalk returns `310000`, or a signature/timestamp error appears | Copy the original signing secret shown by the platform again and synchronize the system clock on the machine running NiuOne. |
| Feishu returns `19022`, DingTalk returns `310000`, or an IP-not-allowed error appears | Add the public egress IP of the NiuOne machine to the bot's allowlist. |
| DingTalk returns `400101`, `400102`, or `400106` | Check that the `access_token` is complete, that the bot is enabled, and that it still belongs to the target group. |
| Telegram reports `chat not found` or lacks permission to send | Start a conversation with the bot first, or add it to the target group/channel and grant permission to post messages, then verify the Chat ID again. |
| The settings page rejects the Webhook | Use the official HTTPS URLs listed above. Do not enter an application-bot API, proxy URL, URL containing a username and password, non-default port, or `#fragment`. |
| A channel is added again after being removed and saved | All fields should show “Not set” and must be entered again. If the credentials may have leaked, revoke or rotate them on the corresponding platform as well. |

NiuOne attempts to send at most once per enabled channel and does not retry automatically, avoiding duplicate execution alerts if a response is lost. Delivery errors are logged only as warnings and do not change cash, positions, or execution logs.

By default, the service listens only on `127.0.0.1`. To access it over a LAN or the public Internet, first configure a reverse proxy, HTTPS, and independent access control. Do not expose the local administration entry point directly.

## Runtime Data and Security

Local data is stored in `.local-data/` by default:

```text
.local-data/
├── dashboard.env          # Local runtime configuration; may contain secrets
├── .venv/                 # Python virtual environment
├── runtime/
│   ├── config.yaml        # Service and model configuration
│   ├── dashboard_admin_token.txt # Bootstrap administrator key when no password is configured
│   ├── *.db               # Local databases
│   ├── cron/              # Scheduled-task state and output
│   └── logs/              # Runtime logs
└── backups/               # Local deployment backups
```

`.local-data/` is ignored by Git. Before committing code, publishing logs, or sharing screenshots, make sure it contains no API keys, administrator credentials, database contents, or other personal data.

## Long-Running Service and Updates

Add the `--service` option to the same one-command startup script to initialize dependencies and register and start a native background service.

macOS / Linux:

```bash
./run.sh --service
```

Windows:

```cmd
run.bat --service
```

This mode uses LaunchAgent on macOS, user-level systemd on Linux, and Task Scheduler on Windows. It manages three processes: the dashboard, scheduled-task runner, and followed-source monitor. Followed-source features that are not enabled remain dormant.

Options can be combined to specify a port or prevent the browser from opening automatically:

```bash
./run.sh --service --port 8877 --no-browser
```

For platform-specific status, restart, uninstall, and unattended-operation instructions, see the [Standalone Operation Guide](docs/STANDALONE_EN.md). For deployment updates, log inspection, backups, and rollback procedures, see the [Deployment, Validation, and Rollback Manual](docs/OPERATIONS_EN.md).

## Project Structure

```text
.
├── app/                    # Domain-organized application source
│   ├── entrypoints/        # Dashboard, scheduler, monitor, and report launchers
│   ├── compat/             # Legacy bare-module adapters
│   ├── core/               # Cross-domain paths, caches, and infrastructure
│   ├── automation/         # Cron rules and scheduled-task orchestration
│   ├── dashboard/          # Dashboard service and APIs
│   ├── market_data/        # Market-data access and security-code utilities
│   ├── messaging/          # Notification channels, dispatch, and trade messages
│   ├── reports/            # A-share and US-market reports
│   ├── monitoring/         # X and other monitoring workflows
│   ├── screening/          # Multi-strategy screening and candidate enrichment
│   ├── storage/            # Message history and practice-trading storage
│   ├── trading/            # Practice trading and optimization
│   └── strategies/         # Strategy registry, scoring, selection, attribution, exits, and prompts
├── config/                 # Runtime policies and security conventions
├── docs/                   # Deployment, operation, and research documentation
├── scripts/                # Validation, deployment, and standalone-task scripts
├── tests/                  # Automated tests
├── tools/                  # Local maintenance tools
├── web/                    # Vue 3 components, Vite configuration, and dependency lock
├── frontend/               # Migration CSS and legacy controller assets
├── dashboard.env.example   # Example configuration
├── run.sh                  # One-command startup for macOS / Linux
├── run.bat                 # One-command startup for Windows
└── requirements.txt        # Python dependencies
```

## Validation

After starting the service, run these health checks:

```bash
curl -s -o /dev/null -w 'HEALTH HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/healthz
curl -s -o /dev/null -w 'SNAPSHOT HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/api/v2/public/latest
```

Both are expected to return `HTTP:200`.

Development validation:

```bash
./scripts/validate.sh
```

The validation script builds the Vue production app, checks the Python, JavaScript, Shell, and Windows BAT entry points, and runs the automated tests under `tests/`.

## Frequently Asked Questions

### `python3` Not Found

Install Python 3.11 or later and confirm that `python3 --version` prints the version correctly. The Windows startup script tries `python`, `py -3`, and `python3` in that order.

### Dependency Installation Fails

The first startup downloads dependencies from PyPI. Check your network connection and local pip configuration, then run the startup script again.

### Port `8787` Is Already in Use

Specify another port:

```bash
./run.sh --port 8877
```

### The Page Is Accessible, but Some Content Is Missing

Check the data sources, model services, feature switches, and task times on the settings page, and confirm that the relevant external services are reachable. For additional troubleshooting, see the [Deployment, Validation, and Rollback Manual](docs/OPERATIONS_EN.md).

## Documentation

- [Strategy Research Notes](docs/strategies/README_EN.md)
- [Standalone Operation Guide](docs/STANDALONE_EN.md)
- [Deployment, Validation, and Rollback Manual](docs/OPERATIONS_EN.md)
- [Runtime Data and Sensitive Information Policy](config/runtime-policy_EN.md)

## License

NiuOne is released under the [Apache License 2.0](LICENSE).
