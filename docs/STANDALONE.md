# 独立运行说明

本文说明如何在本机独立运行 NiuOne。默认运行数据保存在工程目录内的 `.local-data/`，源码和真实数据分开管理。

## 一键启动

```bash
cd /path/to/NiuOne
./run.sh
```

| 系统 | 启动方式 |
|---|---|
| macOS | 终端执行 `./run.sh` |
| Windows | 双击或 CMD 执行 `run.bat` |
| Linux | 终端执行 `./run.sh` |

首次运行会自动完成：

- 创建 `.local-data/`
- 创建 `.local-data/.venv`
- 安装 `requirements.txt`
- 生成 `.local-data/dashboard.env`
- 初始化 `.local-data/runtime/` 下的日志、数据库和任务输出目录

启动后访问：

```text
http://127.0.0.1:8787/
```

`run.sh` 和 `run.bat` 的本地首次运行默认关闭访问认证，管理员密码为空，方便第一次打开就能使用。需要保护设置页时，可以在设置页或启动参数中设置管理员密码。

也可以在一键启动时设置管理员密码，脚本会保存到 `.local-data/dashboard.env`：

```bash
./run.sh --admin-password "change-this-to-a-strong-password"
```

Windows：

```cmd
run.bat --admin-password "change-this-to-a-strong-password"
```

也可以在一键启动时指定 dashboard 端口，脚本会保存到 `.local-data/dashboard.env`：

```bash
./run.sh --port 8877
```

Windows：

```cmd
run.bat --port 8877
```

## 隔离启动

调试或验收时可以使用独立端口和临时运行目录，避免污染真实数据：

```bash
cd /path/to/NiuOne
DASHBOARD_HOME=/tmp/niuone-smoke DASHBOARD_AUTH_ENABLED=0 DASHBOARD_PORT=8877 ./scripts/run_standalone.sh
```

访问：

```text
http://127.0.0.1:8877/
```

`scripts/run_standalone.sh` 不会自动创建虚拟环境，适合在已安装依赖的开发或验证环境中使用。

## 大模型配置

NiuOne 需要接入大模型后才能驱动完整工作流。没有模型配置时，本地页面和部分静态视图可以打开，但事件抓取、信息检索、X 关注列表监控、美股机构评级日报和买卖决策无法完整运行。

推荐配置：

| 场景 | 推荐模型 | 主要配置项 |
|---|---|---|
| X 关注列表监控、美股机构评级日报 | Grok | `DASHBOARD_GROK_BASE_URL`、`DASHBOARD_GROK_API_KEY`、`DASHBOARD_GROK_MODEL`、`X_WATCHLIST_MAX_TOKENS`、`US_RATING_MAX_TOKENS` |
| A 股盘面总结增强 | 兼容 `/chat/completions` 的模型 | `A_SHARE_MODEL_SUMMARY_BASE_URL`、`A_SHARE_MODEL_SUMMARY_API_KEY`、`A_SHARE_MODEL_SUMMARY_MODEL`、`A_SHARE_MODEL_SUMMARY_MAX_TOKENS`；留空时复用 `DASHBOARD_GROK_*` |
| A 股候选股消息面预检 | 具备实时搜索能力的模型 | `DASHBOARD_NEWS_BASE_URL`、`DASHBOARD_NEWS_API_KEY`、`DASHBOARD_NEWS_MODEL`、`DASHBOARD_NEWS_MAX_TOKENS`、`DASHBOARD_NEWS_CONCURRENCY` |
| 选股后的买卖决策 | 推荐 DeepSeek，可用其他兼容模型 | `DASHBOARD_DECISION_BASE_URL`、`DASHBOARD_DECISION_API_KEY`、`DASHBOARD_DECISION_MODEL` |
| 买卖决策情报包 | 本地聚合，不需要额外模型 | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`、`DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`、`DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |

启动后点击页面上的设置按钮，在设置页维护模型、任务时间和推文监控作者。推文监控作者填写 X/Twitter handle，不需要 `@`。
推文监控和美股评级相关设置由“开启牛牛美股”开关控制；关闭时这些设置会折叠隐藏，后台 X 监控和美股评级定时任务会跳过。
`*_CONTEXT_LENGTH` 只表示模型上下文窗口，默认 `128000`；`*_MAX_TOKENS` 只用于本次请求的最大输出长度，默认 `4096`，可按场景覆盖。
消息面预检默认最多并发检查 5 只候选股；若上游限流，可把 `DASHBOARD_NEWS_CONCURRENCY` 调低到 `2` 或 `1`。

买卖决策情报包默认开启，会把盘面监控、隔夜美股、指数/期货、板块涨跌、行业资金、热门股、候选消息面和账户仓位摘要一起写入每次模拟交易决策 prompt 与日志；单个行情源失败时只记录状态，不会阻断本轮决策。

## 运行时文件

默认运行数据位于：

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

`.local-data/` 已被 `.gitignore` 忽略。不要把其中的数据库、本地凭据、日志、模型配置或任务输出提交到 Git。

## 关键配置项

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `DASHBOARD_HOME` | `.local-data/runtime` | 运行数据根目录 |
| `DASHBOARD_HOST` | `127.0.0.1` | 监听地址 |
| `DASHBOARD_PORT` | `8787` | 监听端口 |
| `PYTHON_BIN` | `.local-data/.venv/bin/python` 或 Windows venv Python | Python 可执行文件 |
| `DASHBOARD_CONFIG` | `$DASHBOARD_HOME/config.yaml` | 模型服务商和模型 YAML 配置 |
| `DASHBOARD_PUSH_HISTORY_DB` | `$DASHBOARD_HOME/push_history.db` | 消息历史数据库 |
| `DASHBOARD_PORTFOLIO_STATE` | `$DASHBOARD_HOME/cron/output/niuniu_practice_portfolio.json` | 模拟账户状态 |
| `DASHBOARD_AUTH_ENABLED` | 一键本地启动默认 `0` | 访问认证开关，按需开启 |
| `DASHBOARD_ADMIN_PASSWORD` | 空 | 设置页管理员密码，可留空使用本地备用管理员凭据 |
| `X_WATCHLIST_ACCOUNTS` | 空 | 推文监控作者列表，使用英文逗号分隔 |
| `DASHBOARD_DECISION_INTELLIGENCE_ENABLED` | `1` | 买卖决策是否启用全局情报包 |
| `DASHBOARD_TRADE_DISCIPLINE_TEXT` | 空 | 买卖决策 prompt 的交易纪律文本；为空使用内置默认纪律 |
| `DASHBOARD_MAX_TOTAL_POSITION_PCT` | `80` | 模型仓位参考，不作为执行层硬拦截 |
| `DASHBOARD_MIN_CASH_RESERVE_PCT` | `20` | 模型现金缓冲参考，不作为执行层硬拦截 |

保存设置后，运行时可热应用的配置会立即用于后续请求；需要重启的配置请重启本地服务。

## 后台任务

需要后台托管时通常包含三个进程：

```text
run-dashboard.sh
run-niuone-cron-scheduler.sh
run-x-watchlist-daemon.sh
```

macOS 上如果已安装 LaunchAgent，可以重启：

```bash
launchctl kickstart -k gui/$(id -u)/ai.niuone.dashboard
launchctl kickstart -k gui/$(id -u)/ai.niuone.cron-scheduler
launchctl kickstart -k gui/$(id -u)/ai.niuone.x-watchlist
```

直接生成一次美股机构买入评级日报：

```bash
./scripts/run_us_rating_report.sh
```

## 排查

检查页面是否可访问：

```bash
curl -s -o /dev/null -w 'HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/
```

检查日志：

```bash
ls -lh .local-data/runtime/logs/
tail -n 100 .local-data/runtime/logs/*.log
```

确认真实数据仍被忽略：

```bash
git status --ignored --short
```
