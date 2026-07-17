# 独立运行说明

简体中文 | [English](STANDALONE_EN.md)

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

看板首页和展示数据保持公开访问；设置页与管理 API 始终需要管理员认证。首次启动时，请使用服务自动生成的 bootstrap 管理密钥进入设置页；其路径是 `$DASHBOARD_HOME/dashboard_admin_token.txt`，默认即 `.local-data/runtime/dashboard_admin_token.txt`。登录后可在“访问控制”中设置管理员密码，新密码会立即生效并注销旧会话。也可在启动前直接编辑权限为 `0600` 的 `.local-data/dashboard.env`，设置 `DASHBOARD_ADMIN_PASSWORD`；不要通过命令行参数传递密码。

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
DASHBOARD_HOME=/tmp/niuone-smoke DASHBOARD_PORT=8877 ./scripts/run_standalone.sh
```

访问：

```text
http://127.0.0.1:8877/
```

`scripts/run_standalone.sh` 不会自动创建虚拟环境，适合在已安装依赖的开发或验证环境中使用。

Windows PowerShell 可以通过临时数据目录运行隔离实例：

```powershell
cd C:\path\to\NiuOne
$env:NIUONE_LOCAL_DATA_DIR = Join-Path $env:TEMP "niuone-smoke"
.\run.bat --port 8877 --no-browser
```

测试完成后关闭进程，并按需删除 `$env:TEMP\niuone-smoke`。

## 大模型配置

NiuOne 需要接入大模型后才能驱动完整工作流。没有模型配置时，本地页面和部分静态视图可以打开，但事件抓取、信息检索、X 关注列表监控、美股机构评级日报和买卖决策无法完整运行。

推荐配置：

| 场景 | 推荐模型 | 主要配置项 |
|---|---|---|
| X 关注列表监控、美股机构评级日报 | Grok | `DASHBOARD_GROK_BASE_URL`、`DASHBOARD_GROK_API_KEY`、`DASHBOARD_GROK_MODEL`、`DASHBOARD_GROK_API_MODE`、`X_WATCHLIST_MAX_TOKENS`、`US_RATING_MAX_TOKENS` |
| A 股盘面总结增强 | 兼容 `/chat/completions` 的模型 | `A_SHARE_MODEL_SUMMARY_BASE_URL`、`A_SHARE_MODEL_SUMMARY_API_KEY`、`A_SHARE_MODEL_SUMMARY_MODEL`、`A_SHARE_MODEL_SUMMARY_MAX_TOKENS`；留空时复用 `DASHBOARD_GROK_*` |
| A 股候选股消息面预检 | 具备实时搜索能力的模型 | `DASHBOARD_NEWS_BASE_URL`、`DASHBOARD_NEWS_API_KEY`、`DASHBOARD_NEWS_MODEL`、`DASHBOARD_NEWS_API_MODE`、`DASHBOARD_NEWS_MAX_TOKENS`、`DASHBOARD_NEWS_CONCURRENCY` |
| 问财龙虎榜研究数据 | 同花顺问财 OpenAPI | `IWENCAI_ENABLED`、`IWENCAI_BASE_URL`、`IWENCAI_API_KEY`、`IWENCAI_TIMEOUT_SECONDS`、`IWENCAI_MAX_RETRIES`、`IWENCAI_MAX_CONCURRENCY`、`IWENCAI_CACHE_TTL_SECONDS`、`IWENCAI_DRAGON_TIGER_CRON` |
| 选股后的买卖决策 | 推荐 DeepSeek，可用其他兼容模型 | `DASHBOARD_DECISION_BASE_URL`、`DASHBOARD_DECISION_API_KEY`、`DASHBOARD_DECISION_MODEL` |
| 买卖决策情报包 | 本地聚合，不需要额外模型 | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`、`DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`、`DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |

启动后点击页面上的设置按钮，在设置页维护模型、任务时间和推文监控作者。推文监控作者填写 X/Twitter handle，不需要 `@`。
推文监控和美股评级相关设置由“开启牛牛美股”开关控制；关闭时这些设置会折叠隐藏，后台 X 监控和美股评级定时任务会跳过。
`DASHBOARD_GROK_API_MODE` 默认 `auto`：Grok 4.5 使用带搜索工具的 Responses API，其他模型使用 Chat Completions；也可显式填写 `responses` 或 `chat`。`X_WATCHLIST_REQUEST_TIMEOUT_SECONDS` 默认 `45` 秒。
`DASHBOARD_NEWS_API_MODE` 默认 `auto`：Grok 4.5 和 GPT-5 系列搜索模型使用带 `web_search` 工具的 Responses API；也可显式填写 `responses` 或 `chat`。
`*_CONTEXT_LENGTH` 只表示模型上下文窗口，默认 `128000`；`*_MAX_TOKENS` 表示本次请求的最大输出长度，调用层会按 Chat 或 Responses 接口映射兼容参数。JSON 与 SSE 返回均受支持。
消息面预检默认最多并发检查 5 只候选股；若上游限流，可把 `DASHBOARD_NEWS_CONCURRENCY` 调低到 `2` 或 `1`。
问财数据源默认关闭；在管理设置页的“问财数据源”中启用并保存密钥后，可在 `/dragon-tiger` 按交易日查看龙虎榜买卖前五的机构、营业部及问财明确标注的游资/量化席位与金额，也可通过 `/api/iwencai/dragon-tiger` 按日期读取研究快照。Cron 默认在 A 股交易日北京时间 18:00 更新最新快照，并按 `iwencai_dragon_tiger/YYYY-MM-DD.json` 归档；空结果或失败保留上一份有效数据，同日席位明细失败不会覆盖已归档记录。密钥只保存在本机私有 `dashboard.env`，页面不会回显。

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
| `DASHBOARD_ADMIN_PASSWORD` | 空 | 设置页管理员密码；为空时使用 `$DASHBOARD_HOME/dashboard_admin_token.txt` 中的 bootstrap 管理密钥 |
| `PYTHON_BIN` | `.local-data/.venv/bin/python` 或 Windows venv Python | Python 可执行文件 |
| `DASHBOARD_CONFIG` | `$DASHBOARD_HOME/config.yaml` | 模型服务商和模型 YAML 配置 |
| `DASHBOARD_PUSH_HISTORY_DB` | `$DASHBOARD_HOME/push_history.db` | 消息历史数据库 |
| `DASHBOARD_PORTFOLIO_STATE` | `$DASHBOARD_HOME/cron/output/niuniu_practice_portfolio.json` | 模拟账户状态 |
| `X_WATCHLIST_ACCOUNTS` | 空 | 推文监控作者列表，使用英文逗号分隔 |
| `DASHBOARD_DECISION_INTELLIGENCE_ENABLED` | `1` | 买卖决策是否启用全局情报包 |
| `DASHBOARD_TRADE_DISCIPLINE_TEXT` | 空 | 买卖决策 prompt 的交易纪律文本；为空使用内置默认纪律 |
| `DASHBOARD_MAX_TOTAL_POSITION_PCT` | `80` | 全局总仓上限；`zettaranc` 和 `sector_tide` 在执行层取全局限制与策略套件硬上限中的更严格值，其他套件主要作为模型参考 |
| `DASHBOARD_MIN_CASH_RESERVE_PCT` | `20` | 全局现金缓冲；`zettaranc` 和 `sector_tide` 在执行层同时校验，其他套件主要作为模型参考 |

保存设置后，运行时可热应用的配置会立即用于后续请求；需要重启的配置请重启本地服务。

## 独立进程与长期运行

完整后台运行通常由三个相互独立的进程组成：

| 进程 | macOS / Linux 入口 | Windows 入口 | 是否必需 |
|---|---|---|---|
| Dashboard | `run-dashboard.sh` | `run.bat --no-browser --skip-install` | 是 |
| 定时调度器 | `run-niuone-cron-scheduler.sh` | `.local-data\.venv\Scripts\python.exe app\niuone_cron_scheduler.py` | 启用自动摘要、数据库入库或模拟持仓自动离场检查时需要 |
| 关注源守护进程 | `run-x-watchlist-daemon.sh` | `.local-data\.venv\Scripts\python.exe app\x_watchlist_daemon.py` | 启用 X 关注列表时需要 |

实战 B1 选股计划运行在 Dashboard 进程内；定时调度器不负责选股，但负责独立的模拟持仓自动离场检查。要让模拟账户完整走通“定时选股—决策—自动离场”，Dashboard 与定时调度器都必须持续运行。

### 一键启用

`--service` 会先执行与普通启动相同的目录初始化、虚拟环境创建和依赖安装，再注册当前平台的原生服务并立即启动。重复执行会更新已有注册，适合代码或配置变更后重新部署。

macOS / Linux：

```bash
./run.sh --service
```

Windows：

```cmd
run.bat --service
```

可以与其他参数组合：

```bash
./run.sh --service --port 8877 --no-browser
```

```cmd
run.bat --service --port 8877 --no-browser
```

三个进程都会被注册。关闭“牛牛美股”功能后，X 关注源守护进程会跳过采集并保持低频休眠，无需单独卸载。

### 状态、重启与卸载

macOS / Linux：

```bash
./scripts/manage-long-running.sh status
./scripts/manage-long-running.sh restart
./scripts/manage-long-running.sh uninstall
```

Windows PowerShell：

```powershell
powershell -File .\scripts\manage-long-running.ps1 -Action Status
powershell -File .\scripts\manage-long-running.ps1 -Action Restart
powershell -File .\scripts\manage-long-running.ps1 -Action Uninstall
```

卸载操作只移除服务或计划任务，不删除 `.local-data/` 中的配置、数据库和日志。

### 平台行为

| 平台 | 实现 | 自动启动行为 | 服务日志 |
|---|---|---|---|
| macOS | `~/Library/LaunchAgents/ai.niuone.*.plist` | 当前用户登录后启动，异常退出后自动重启 | `.local-data/runtime/logs/ai.niuone.*.log` |
| Linux | `~/.config/systemd/user/niuone-*.service` | 用户级 systemd 启动，脚本会尝试启用 linger | `journalctl --user -u niuone-dashboard.service` |
| Windows | `NiuOne *` 计划任务 | 当前用户登录后启动，异常退出后自动重试 | `.local-data\runtime\logs\windows-service-*.log` |

Linux 如果提示无法启用 linger，可在取得相应授权后执行：

```bash
loginctl enable-linger "$USER"
```

Windows 默认采用“用户登录时启动”，避免把 Windows 登录密码写入命令。无人值守主机如需开机后未登录也运行，可在“任务计划程序”中把触发器改为“启动时”，选择“不管用户是否登录都要运行”，并由 Windows 安全地保存运行账户凭据。建议使用专门的普通用户账户，不要改成 `SYSTEM`。

## 排查

macOS / Linux 检查页面是否可访问：

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

Windows PowerShell 检查页面和计划任务：

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/).StatusCode
Get-ScheduledTask -TaskName "NiuOne*" | Get-ScheduledTaskInfo
```

检查最近日志：

```powershell
Get-ChildItem .\.local-data\runtime\logs\*.log |
  ForEach-Object {
    "=== $($_.Name) ==="
    Get-Content $_.FullName -Tail 100
  }
```

如果计划任务显示 `Ready` 但页面无法访问，可先手动执行 `.\run.bat --no-browser --skip-install` 查看控制台错误，再检查端口占用、Python 虚拟环境和 `.local-data\dashboard.env`。
