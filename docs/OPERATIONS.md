# 部署、验证和回滚手册

简体中文 | [English](OPERATIONS_EN.md)

本文档记录 NiuOne 的本地运行、验证、部署、日志检查和回滚流程。真实运行数据统一保存在 `.local-data/`，该目录不进入 Git。

## 1. 目录约定

```text
/path/to/NiuOne/
├── app/                    # 本地服务和任务源码
├── tests/                  # 单元测试
├── scripts/                # 验证、部署和任务脚本
├── docs/                   # 文档
├── config/                 # 运行策略说明
├── .local-data/            # 本机真实运行数据，Git ignored
├── run.sh                  # macOS/Linux 一键启动
├── run.bat                 # Windows BAT 一键启动
├── run-dashboard.sh        # 网页服务启动入口
├── run-niuone-cron-scheduler.sh
└── run-x-watchlist-daemon.sh
```

运行数据默认位于：

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

不要把 `.local-data/` 中的数据库、本地凭据、日志、模型配置或归档内容提交到 Git，也不要复制到公开上下文。

## 2. 运行前检查

一键启动：

```bash
./run.sh
```

看板首页和展示数据保持公开访问；设置页与管理 API 始终需要管理员认证。配置了 `DASHBOARD_ADMIN_PASSWORD` 时使用该密码；否则使用服务自动生成的 bootstrap 管理密钥。本地密钥位于 `$DASHBOARD_HOME/dashboard_admin_token.txt`（默认 `.local-data/runtime/dashboard_admin_token.txt`），Docker 中位于 `/data/runtime/dashboard_admin_token.txt`。

首次启动时，读取 `$DASHBOARD_HOME/dashboard_admin_token.txt` 中的 bootstrap 管理密钥进入设置页，然后在“访问控制”中设置管理员密码。新密码会立即生效并注销旧会话。也可在启动前直接编辑权限为 `0600` 的 `.local-data/dashboard.env`，设置 `DASHBOARD_ADMIN_PASSWORD`；不要通过命令行参数传递密码，以免进入 shell 历史或进程列表。

如需指定 dashboard 端口：

```bash
./run.sh --port 8877
```

Windows 使用 `run.bat --port 8877`。

首次运行会创建 `.local-data/.venv`、安装依赖、生成 `.local-data/dashboard.env`，然后启动：

```text
http://127.0.0.1:8787/
```

管理员密码会保存到 `.local-data/dashboard.env`；请将密码和 bootstrap 管理密钥都视为敏感凭据，不要提交或复制到公开上下文。

## 3. 模型配置

NiuOne 需要大模型驱动完整工作流。X 关注列表监控和美股机构评级日报推荐使用 Grok；A 股盘面总结增强可使用任意兼容 `/chat/completions` 的模型；A 股候选股消息面预检可独立配置具备实时搜索能力的模型；选股后的买卖决策可配置兼容模型，推荐使用 DeepSeek。

核心配置项：

| 场景 | 配置项 |
|---|---|
| 牛牛美股总开关 | `DASHBOARD_US_FEATURES_ENABLED` |
| Grok API | `DASHBOARD_GROK_BASE_URL`、`DASHBOARD_GROK_API_KEY`、`DASHBOARD_GROK_MODEL`、`DASHBOARD_GROK_API_MODE`、`DASHBOARD_GROK_CONTEXT_LENGTH` |
| A 股盘面模型总结单独覆盖 | `A_SHARE_MODEL_SUMMARY_BASE_URL`、`A_SHARE_MODEL_SUMMARY_API_KEY`、`A_SHARE_MODEL_SUMMARY_MODEL`、`A_SHARE_MODEL_SUMMARY_MAX_TOKENS` |
| 消息面预检 API | `DASHBOARD_NEWS_BASE_URL`、`DASHBOARD_NEWS_API_KEY`、`DASHBOARD_NEWS_MODEL`、`DASHBOARD_NEWS_API_MODE`、`DASHBOARD_NEWS_MAX_TOKENS`、`DASHBOARD_NEWS_CONCURRENCY` |
| 问财内置数据源 | `IWENCAI_ENABLED`、`IWENCAI_BASE_URL`、`IWENCAI_API_KEY`、`IWENCAI_TIMEOUT_SECONDS`、`IWENCAI_MAX_RETRIES`、`IWENCAI_MAX_CONCURRENCY`、`IWENCAI_CACHE_TTL_SECONDS`、`IWENCAI_DRAGON_TIGER_CRON` |
| 买卖决策 API | `DASHBOARD_DECISION_BASE_URL`、`DASHBOARD_DECISION_API_KEY`、`DASHBOARD_DECISION_MODEL` |
| 买卖决策情报包 | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`、`DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`、`DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |
| 买卖决策交易纪律 | `DASHBOARD_TRADE_DISCIPLINE_TEXT`；为空时使用内置默认纪律，填写后进入模型 prompt 的“必须遵守”段 |
| 模拟账户节奏与仓位参考 | `DASHBOARD_MAX_OPEN_POSITIONS`、`DASHBOARD_MAX_NEW_BUYS_PER_DECISION`、`DASHBOARD_MAX_SINGLE_POSITION_PCT`、`DASHBOARD_MAX_TOTAL_POSITION_PCT`、`DASHBOARD_MIN_CASH_RESERVE_PCT`；默认作为模型参考，Z 哥和板块潮汐等注册硬限制策略会在模拟执行层取全局与策略限制的更严格值 |
| 美股评级单独覆盖 | `US_RATING_BASE_URL`、`US_RATING_API_KEY`、`US_RATING_MODEL`、`US_RATING_MAX_TOKENS` |
| X 关注列表单独覆盖 | `X_WATCHLIST_BASE_URL`、`X_WATCHLIST_API_KEY`、`X_WATCHLIST_MODEL`、`X_WATCHLIST_MAX_TOKENS` |

完成管理员认证后，优先通过页面上的设置按钮进入设置页维护。推文监控和美股评级相关设置由“开启牛牛美股”开关控制；关闭时设置页会隐藏这些项，后台 X 监控和美股评级定时任务也会跳过。也可以直接编辑 `.local-data/dashboard.env`，保存后按配置影响范围重启或等待下一轮任务读取。
`DASHBOARD_GROK_API_MODE` 可设为 `auto`、`responses` 或 `chat`。默认 `auto` 会为 Grok 4.5 使用带 `web_search`/`x_search` 工具的 Responses API，其他模型保持 Chat Completions；兼容网关可显式选择对应模式。`X_WATCHLIST_REQUEST_TIMEOUT_SECONDS` 控制 X 单账号请求超时，默认 `45` 秒。
`DASHBOARD_NEWS_API_MODE` 同样可设为 `auto`、`responses` 或 `chat`。默认 `auto` 会为 Grok 4.5 和 GPT-5 系列搜索模型使用带 `web_search` 工具的 Responses API；其他模型保持 Chat Completions，也可按网关能力显式选择。
`*_CONTEXT_LENGTH` 仅表示模型上下文窗口，默认 `128000`；`*_MAX_TOKENS` 表示期望的最大输出长度，调用层会按接口映射为 `max_tokens` 或 `max_output_tokens`。已知不接受 Responses 输出长度参数的 GPT-5.6 网关别名会省略该参数，其他网关若明确返回不支持也会自动去参重试一次。模型响应同时兼容 JSON 和 SSE，即使网关在 `stream=false` 时仍强制返回 SSE。
消息面预检默认最多并发检查 5 只候选股；如果上游出现限流或 403/429，可将 `DASHBOARD_NEWS_CONCURRENCY` 降为 `2` 或 `1`。

问财数据源默认关闭。启用并配置 API Key 后，Dashboard 提供固定用途的
`/api/iwencai/dragon-tiger?date=YYYY-MM-DD&page=1&limit=100` 龙虎榜接口；接口不接受任意自然语言问句，
单页最多 100 只股票，并复用 Dashboard 限流和缓存。返回结果按股票代码去重，`sector` 提供所属行业，重复榜单记录保留在 `details` 中。`seats` 保留买卖前五的机构专用、普通营业部及问财明确标注的游资/量化席位，并记录同一营业部可能同时出现的 `buy_rank`、`sell_rank` 和金额；`institution_seats` 继续提供机构子集以兼容现有消费者。席位明细失败不会阻断股票榜单，也不会覆盖同一交易日已经归档的有效席位记录。
问财响应属于研究数据快照，发生超时、计数不一致或上游失败时会返回明确状态，不会覆盖账户、成交或其他真实交易记录。Dashboard 的 `/dragon-tiger` 栏目可按交易日切换，并优先读取精确日期归档；Cron 默认在 A 股交易日北京时间
18:00 更新 `.local-data/runtime/cron/output/iwencai_dragon_tiger_latest.json`，同时写入 `.local-data/runtime/cron/output/iwencai_dragon_tiger/YYYY-MM-DD.json`。空结果或主榜失败不会覆盖上一份有效快照或历史归档。

买卖决策情报包默认开启。每次实战选股扫描后的模型决策都会读取盘面监控、隔夜美股、指数行情、板块涨跌、行业资金、热门股、候选消息面和账户仓位摘要，并把压缩后的 `decision_intelligence` 写入模拟交易决策日志。行情源失败时会保留 `source_status`，本轮决策继续按可用信息和既有风控执行。

实战页面的规范地址为 `/?category=practice`，候选查询与刷新接口分别为 `/api/practice_candidates` 和 `/api/practice_candidates/refresh`。旧的 `category=b1_screen` 与 `/api/b1_screen` 路径仅作为兼容入口保留。

### 3.1 实战策略调度与进程归属

实战策略没有各自独立的选股定时任务。Dashboard 内置的 B1 调度器在每个计划时间启动统一扫描器，扫描器读取 `DASHBOARD_ACTIVE_STRATEGY`，只运行当前策略套件的评分器。扫描成功后，定时流程同步执行模型判断和模拟执行层复核。

| 配置 | 默认值 | 影响范围 | 生效方式 |
|---|---|---|---|
| `DASHBOARD_ACTIVE_STRATEGY` | `zettaranc` | 当前新候选、模型 Prompt 和新买入规则 | 运行时热应用；下一轮扫描生效 |
| `DASHBOARD_B1_SCHEDULE_ENABLED` | `1` | 是否启动 Dashboard 内置选股调度线程 | 需要重启 Dashboard |
| `DASHBOARD_B1_SCHEDULE_TIMES` | `09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50` | 选股及买卖决策时间点 | 运行时热应用 |
| `DASHBOARD_B1_SCHEDULE_CATCHUP_MINUTES` | `35` | Dashboard 短暂离线后的漏触发补跑窗口 | 需要重启 Dashboard |
| `DASHBOARD_B3_EXIT_TIME` | `09:37` | 开盘自动离场检查 | 后续 Cron 周期读取 |
| `DASHBOARD_TIME_EXIT_TIME` | `14:45` | 尾盘自动离场和时间窗检查 | 后续 Cron 周期读取 |

09:25 扫描处于开盘竞价结束后的静默期。系统可以生成候选和模型动作，但不会直接按竞价参考价记成交；需要执行的动作会排队，09:30 后由 Dashboard 的延迟决策线程重新检查交易时段、最新价格、现金和策略风险预算。

用户可在实战页面点击“手动触发选股及买卖策略”运行完整链路。该操作与定时流程使用同一扫描器、策略配置和执行层，不是绕过风控的强制成交入口。页面普通刷新仅读取缓存与账户状态。

本地自动退出由独立 Cron Scheduler 进程调用。结构止损、板块潮汐退潮、策略时间窗、2R 和 2ATR 等是定时检查，不是实时逐笔监控；要启用这些检查，Dashboard 和 Cron Scheduler 两个进程都必须运行。

排查“策略没有触发”时依次检查：

1. `.local-data/dashboard.env` 中 `DASHBOARD_ACTIVE_STRATEGY` 是否为预期套件；
2. `DASHBOARD_B1_SCHEDULE_ENABLED` 是否开启，Dashboard 进程是否仍在运行；
3. 当前时间是否进入 `DASHBOARD_B1_SCHEDULE_TIMES` 的时间点或补跑窗口；
4. `.local-data/runtime/cron/state/b1_schedule_state.json` 中对应时间槽是 `ok`、`error` 还是 `skipped`；
5. `.local-data/runtime/cron/output/multi_strategy_latest.json` 是否包含最新 `generated_at`、当前策略候选和所需上下文字段；
6. 自动退出未运行时，确认 Cron Scheduler 进程及 `.local-data/runtime/logs/niuone_cron_scheduler.log`。

板块潮汐的用户规则、风险预算和开发者数据契约见[策略研究说明](strategies/README.md#34-板块潮汐)。

## 4. 验证流程

```bash
./scripts/validate.sh
```

验证内容：

1. Python 语法检查
2. 内嵌前端 JavaScript 语法检查
3. Shell 启动脚本语法检查
4. Windows BAT 入口检查
5. `tests/` 单元测试

隔离实例验证：

```bash
DASHBOARD_HOME=/tmp/niuone-smoke DASHBOARD_PORT=8878 ./scripts/run_standalone.sh
```

健康检查：

```bash
curl -s -o /dev/null -w 'HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8878/
curl -s -o /dev/null -w 'HTTP:%{http_code} TOTAL:%{time_total}\n' 'http://127.0.0.1:8878/api/messages?limit=1'
```

预期均返回 `HTTP:200`。

## 5. 本机长期运行

通过一键启动入口注册并启动当前平台的长期运行服务：

```bash
./run.sh --service
```

Windows：

```cmd
run.bat --service
```

macOS / Linux 查看状态或重启：

```bash
./scripts/manage-long-running.sh status
./scripts/manage-long-running.sh restart
```

Windows PowerShell：

```powershell
powershell -File .\scripts\manage-long-running.ps1 -Action Status
powershell -File .\scripts\manage-long-running.ps1 -Action Restart
```

macOS 使用 LaunchAgent，Linux 使用用户级 systemd，Windows 使用任务计划程序。安装位置、无人值守运行、日志和卸载方式见 [独立运行说明](STANDALONE.md)。

## 6. 部署流程

Docker Hub 镜像的构建、版本标签和推送方式见 [容器镜像发布流程](CONTAINER_RELEASE.md)。

本机部署脚本：

```bash
cd /path/to/NiuOne
./scripts/deploy_to_live.sh
```

该脚本会：

- 先运行 `./scripts/validate.sh`
- 备份当前 `app/`、本地环境文件和 `run-dashboard.sh` 到 `.local-data/backups/`
- 确保运行目录存在
- 对当前 `127.0.0.1:8787` 服务进程发送 `HUP`
- 访问 `/` 做 smoke check

如果服务由长期运行模式托管，`HUP` 后通常会由平台服务管理器拉起新进程；如果没有托管器，请手动重新运行 `./run.sh` 或对应启动脚本。

部署后检查：

```bash
curl -s -o /dev/null -w 'HOME HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/
curl -s "http://127.0.0.1:8787/api/messages?limit=1" | python3 -m json.tool | head
```

`/api/messages` 返回中的 `db_path` 应指向工程目录内的 `.local-data/runtime/push_history.db`。

## 7. 日志和任务检查

常用日志目录：

```text
.local-data/runtime/logs/
```

常用状态和输出目录：

```text
.local-data/runtime/cron/state/
.local-data/runtime/cron/output/
```

任务脚本：

```bash
./run-niuone-cron-scheduler.sh
./run-x-watchlist-daemon.sh
./scripts/run_us_rating_report.sh
```

X 关注列表作者通过设置页里的“推文监控作者”维护，填写 handle 时不需要 `@`。

## 8. 回滚

部署备份默认位于：

```text
.local-data/backups/
```

手动回滚 `app/` 示例：

```bash
cp -R .local-data/backups/<backup-name>/app/. app/
./scripts/validate.sh
launchctl kickstart -k gui/$(id -u)/ai.niuone.dashboard
```

如果要回滚 Git 提交，优先使用非破坏性命令：

```bash
git revert <commit-sha>
./scripts/validate.sh
git push origin main
```

回滚后检查：

```bash
curl -s -o /dev/null -w 'HTTP:%{http_code}\n' http://127.0.0.1:8787/
```

## 9. 常见问题

### 页面无法启动

检查：

```bash
./run.sh --no-browser
```

确认 Python 可用、依赖安装成功、端口未被占用。

### 页面能打开但没有历史消息

检查消息库：

```bash
ls -lh .local-data/runtime/push_history.db
curl -s "http://127.0.0.1:8787/api/messages?limit=5" | python3 -m json.tool | head
```

当前消息流以 `push_history.db` 为主要来源。任务脚本需要正常写入该数据库后，页面才会出现对应消息。

盘面监控、X 监控和美股机构评级的新记录只写入该数据库，不再生成 Markdown 文件。升级前已有的 `.md` 历史文件会原样保留，但页面不会读取它们，也不会自动删除。

### 任务没有自动更新

检查三个方向：

```bash
launchctl print gui/$(id -u)/ai.niuone.cron-scheduler | sed -n '1,100p'
launchctl print gui/$(id -u)/ai.niuone.x-watchlist | sed -n '1,100p'
tail -n 200 .local-data/runtime/logs/*.log
```

同时确认模型密钥、任务时间和推文监控作者已经配置。

### 修改前端后页面空白

运行：

```bash
./scripts/validate.sh
```

该脚本会检查 `frontend/` JavaScript、`app/` Python、Shell/PowerShell 入口及完整单元测试。

### 不要提交真实数据

提交前检查：

```bash
git status --ignored --short
```

`.local-data/` 应显示为 ignored，不应出现在 staged files 中。

## 10. 维护原则

1. 改动源码后运行 `./scripts/validate.sh`。
2. 临时测试使用独立 `DASHBOARD_HOME=/tmp/...` 和非 8787 端口。
3. 看板保持公开访问，设置页与管理 API 必须始终通过管理员认证。
4. 真实数据库、本地凭据、日志、模型配置只留在 `.local-data/`。
5. 消息类新任务应直接写入 `push_history.db`，不要生成独立 Markdown 历史文件。
