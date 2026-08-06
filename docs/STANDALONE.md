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
- 使用锁定依赖构建 `web/` 下的 Vue 3/Vite 前端
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

### 中国大陆首次安装超时

如果首次运行在 `pip install` 阶段出现连接或读取超时，通常是当前网络访问 PyPI 不稳定，并非依赖缺失。可以在运行 `run.bat` 前配置用户级 pip 镜像，并为网络请求设置有限的超时和重试次数。以下命令以[清华大学开源软件镜像站](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)为例：

```cmd
python -m pip config --user set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
python -m pip config --user set global.timeout 60
python -m pip config --user set global.retries 10
python -m pip config debug
```

如果系统只提供 Python Launcher，请将命令中的 `python` 替换为 `py -3`。上述设置会写入用户级 `%APPDATA%\pip\pip.ini`，等价内容如下：

```ini
[global]
index-url = https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
timeout = 60
retries = 10
```

确认 `pip config debug` 显示预期配置后，重新运行 `run.bat`。也可以改用所在网络可信且支持 HTTPS 的其他镜像；不要通过 `trusted-host` 或 HTTP 绕过证书验证。pip 配置文件位置和覆盖优先级详见 [pip 官方配置文档](https://pip.pypa.io/en/stable/topics/configuration/)。

公开页面和完整设置页使用同一个 FastAPI/Uvicorn 进程与端口；默认分别位于 `8787/` 和 `8787/admin`。Vue 开发服务器 `5173` 仅用于本机热更新，不参与生产部署。设置页可以通过域名访问，但配置与操作 API 仍需要管理员密码会话。增量快照和 CDN 配置详见 [Dashboard 增量展示与部署](DASHBOARD_V2.md)。

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

`scripts/run_standalone.sh` 不会自动创建 Python 虚拟环境，但会在需要时构建 Vue 前端，适合在已安装 Python、Node.js 与依赖的开发或验证环境中使用。

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
| X 关注列表监控 | Grok | `X_WATCHLIST_ENABLED`、`DASHBOARD_GROK_BASE_URL`、`DASHBOARD_GROK_API_KEY`、`DASHBOARD_GROK_MODEL`、`DASHBOARD_GROK_API_MODE`、`X_WATCHLIST_MAX_TOKENS` |
| 美股机构评级日报 | 具备实时搜索能力的模型；留空时复用 Grok | `US_RATING_MODEL`、`US_RATING_BASE_URL`、`US_RATING_API_KEY`、`US_RATING_MAX_TOKENS` |
| A 股盘面总结增强 | 兼容 `/chat/completions` 的模型 | `A_SHARE_MODEL_SUMMARY_BASE_URL`、`A_SHARE_MODEL_SUMMARY_API_KEY`、`A_SHARE_MODEL_SUMMARY_MODEL`、`A_SHARE_MODEL_SUMMARY_MAX_TOKENS`；留空时复用 `DASHBOARD_GROK_*` |
| A 股候选股及龙虎榜连板/连榜消息面预检 | 具备实时搜索能力的模型 | `DASHBOARD_NEWS_BASE_URL`、`DASHBOARD_NEWS_API_KEY`、`DASHBOARD_NEWS_MODEL`、`DASHBOARD_NEWS_API_MODE`、`DASHBOARD_NEWS_MAX_TOKENS`、`DASHBOARD_NEWS_CONCURRENCY` |
| 问财龙虎榜研究数据 | 同花顺问财 OpenAPI | `IWENCAI_ENABLED`、`IWENCAI_BASE_URL`、`IWENCAI_API_KEY`、`IWENCAI_TIMEOUT_SECONDS`、`IWENCAI_MAX_RETRIES`、`IWENCAI_MAX_CONCURRENCY`、`IWENCAI_CACHE_TTL_SECONDS`、`IWENCAI_DRAGON_TIGER_CRON` |
| 选股后的买卖决策 | 推荐 DeepSeek，可用其他兼容模型 | `DASHBOARD_DECISION_BASE_URL`、`DASHBOARD_DECISION_API_KEY`、`DASHBOARD_DECISION_MODEL` |
| 买卖决策情报包 | 本地聚合，不需要额外模型 | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`、`DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`、`DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |

启动后点击页面上的设置按钮，在设置页维护模型、任务时间和推文监控作者。所有需要模型和 API Key 的分组均可点击“测试模型连接”，测试页面当前填写值但不会自动保存；API Key 留空时复用已保存密钥。推文监控作者填写 X/Twitter handle，不需要 `@`。
推文监控和美股评级相关设置由“开启牛牛美股”总开关控制；关闭时这些设置会折叠隐藏，后台 X 监控和美股评级定时任务会跳过。只关闭“开启 X 关注列表监控”时，X 守护进程和直接执行入口都会跳过查询，美股评级日报仍按计划运行。
`DASHBOARD_GROK_API_MODE` 默认 `auto`：Grok 4.5 使用带搜索工具的 Responses API，其他模型使用 Chat Completions；也可显式填写 `responses` 或 `chat`。`X_WATCHLIST_REQUEST_TIMEOUT_SECONDS` 默认 `45` 秒。
`DASHBOARD_NEWS_API_MODE` 默认 `auto`：Grok 4.5 和 GPT-5 系列搜索模型使用带 `web_search` 工具的 Responses API；Grok Responses 预检模型还会加入 `x_search`。其他模型以 `web_search` 检索可公开索引的雪球/X 页面，不会回退到 `DASHBOARD_GROK_*`。
`*_CONTEXT_LENGTH` 只表示模型上下文窗口，默认 `128000`；`*_MAX_TOKENS` 表示本次请求的最大输出长度，调用层会按 Chat 或 Responses 接口映射兼容参数。JSON 与 SSE 返回均受支持。
消息面预检默认最多并发检查 5 只候选股；若上游限流，可把 `DASHBOARD_NEWS_CONCURRENCY` 调低到 `2` 或 `1`。
问财数据源默认关闭；“问财数据源”设置分组可通过“测试问财接口”使用页面当前地址和密钥发送一次轻量只读查询，不会保存配置或改写龙虎榜快照。启用并保存密钥后，可在 `/dragon-tiger` 按交易日实时查询龙虎榜买卖前五的机构、营业部及问财明确标注的游资/量化席位与金额，也可通过 `/api/iwencai/dragon-tiger` 查询指定日期。问财返回涨停原因时，详情卡会将涨停原因及原因类别与上榜原因分开显示。当日数据及下一次成功查询前保留的最近数据无需密码，更早日期需要输入管理员密码；当日实时回源为空时仍展示最近成功快照。Cron 默认在 A 股交易日北京时间 18:00 更新最新快照，也可通过 `IWENCAI_DRAGON_TIGER_CRON` 调整；消息面预检以该配置对应的本次龙虎榜计划查询时间为起点，而不是上游响应的 `generated_at`，并按交易日持久化连板股票（`limit_up_streak >= 2`）或连续上榜股票（`consecutive_listed = true` 且 `consecutive_list_days >= 2`）的已检索和待检索状态。调度器启动时会追补最近应有的交易日快照，每次新拉取前也会补检上一份快照，周五遗漏因此可在周末重启或下一交易日前完成；全部完成后同日不再调用模型。最近一次非空成功查询会保留至下一次成功查询并被原子覆盖，空结果或失败继续保留上一份有效数据。下一次成功更新还会清理旧版本生成的日期归档；同日席位明细失败不会覆盖当前快照中的有效记录。密钥只保存在本机私有 `dashboard.env`，页面不会回显。

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
| `DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON` | `5 9 * * 1-5` | Scheduler 启动时立即预检，周一至周五 09:05 再校验严格前向协议 |
| `DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON` | `15 15 * * 1-5` | 实际 A 股运行日盘后刷新行情并保存无交易副作用的账户权益快照 |
| `DASHBOARD_NIUONE_FORWARD_CRON` | `20 15 * * 1-5` | 周一至周五盘后从耐久成交账本重算牛牛严格前向报告；下一轮 Cron 生效 |
| `DASHBOARD_NIUONE_FORWARD_COHORT_START` | `2026-08-04` | 严格前向队列起始日；修改规则时归档旧协议锁并从新交易日重新累计 |
| `DASHBOARD_ACTIVE_STRATEGY` | `niuone` | 当前独立策略；保存后下一轮扫描热生效 |
| `DASHBOARD_PRACTICE_SCHEDULE_TIMES` | `09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50` | 盘面总结、选股和模拟决策的共享时间点 |
| `DASHBOARD_KLINE_BOOTSTRAP_ENABLED` | `1` | 首次部署或缓存过期后立即准备全市场日 K；重启生效 |
| `DASHBOARD_KLINE_READINESS_MIN_COVERAGE_PERCENT` | `90` | 实战扫描放行所需的日期有效日 K 覆盖率；允许 90～100，重启生效 |
| `DASHBOARD_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS` | `90` | 全市场实时行情阶段总预算；允许 15～300 秒，重启生效 |
| `DASHBOARD_MANUAL_DATA_INITIALIZATION_TIMEOUT_SECONDS` | `660` | 手动任务等待日 K 初始化完成的最长秒数；重启生效 |
| `X_WATCHLIST_ACCOUNTS` | 空 | 推文监控作者列表，使用英文逗号分隔 |
| `DASHBOARD_DECISION_INTELLIGENCE_ENABLED` | `1` | 买卖决策是否启用全局情报包 |
| `DASHBOARD_TRADE_DISCIPLINE_TEXT` | 空 | 买卖决策 prompt 的交易纪律文本；为空使用内置默认纪律 |
| `DASHBOARD_MAX_TOTAL_POSITION_PCT` | `80` | 全局总仓上限；`zettaranc` 和 `sector_tide` 在执行层取全局限制与策略套件硬上限中的更严格值，其他套件主要作为模型参考 |
| `DASHBOARD_MIN_CASH_RESERVE_PCT` | `20` | 全局现金缓冲；`zettaranc` 和 `sector_tide` 在执行层同时校验，其他套件主要作为模型参考 |
| `DASHBOARD_NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED` | `1` | 是否复用全市场行情采样刷新题材强度；重启生效 |
| `DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS` | `30` | 题材强度和市场情绪共享的全市场采样间隔，允许 `30`～`600` 秒；重启生效 |
| `DASHBOARD_AUTO_VERSION_CHECK_ENABLED` | `1` | 页面加载时是否检查 Docker Hub 新版本；运行时热生效，不会自动安装更新 |

保存设置后，运行时可热应用的配置会立即用于后续请求；需要重启的配置请重启本地服务。

## 独立进程与长期运行

完整后台运行通常由三个相互独立的进程组成：

| 进程 | macOS / Linux 入口 | Windows 入口 | 是否必需 |
|---|---|---|---|
| Dashboard | `run-dashboard.sh` | `run.bat --no-browser --skip-install` | 是 |
| 定时调度器 | `run-niuone-cron-scheduler.sh` | `.local-data\.venv\Scripts\python.exe app\entrypoints\niuone_cron_scheduler.py` | 启用自动摘要、数据库入库或模拟持仓自动离场检查时需要 |
| 关注源守护进程 | `run-x-watchlist-daemon.sh` | `.local-data\.venv\Scripts\python.exe app\entrypoints\x_watchlist_daemon.py` | 启用 X 关注列表时需要 |

实战 B1 选股计划运行在 Dashboard 进程内；每个计划时间会在买卖决策前同步生成统一的“此刻盘面总结与评价”，其风险标签直接作为实战交易上下文。页面按钮和手动选股与交易链路也使用同一生成器。定时调度器不负责选股，但会在启动时及工作日 09:05、首轮 09:25 决策之前冻结/校验严格前向协议和起始日前零持仓账户基线，随后负责独立的模拟持仓自动离场检查、15:15 无交易盘后净值快照，并在 15:20 从 `niuniu.db` 完整成交、候选机会集、每日权益与决策 payload 加最近 JSON 日志生成私有牛牛严格前向报告。协议 v18 要求每个 Practice 槽不仅终态为 `ok`，还必须有结构完整的 SQLite 决策证据；延迟成交沿用原槽候选分母，报告按五阶段输出观察、入选、模型 BUY、实际 BUY、定仓利用率和拒单分类。落盘或 schema 校验失败会使该槽或自动退出任务失败。冻结指纹覆盖三个前向 Cron、耐久数据库/恢复状态/运行审计/交易所日历缓存有效路径和调度/存储/评估源码；路径只保存摘要，`--as-of` 不能改变锁的实际冻结日期。只有全部完成生命周期的入口归因完整、并且起始日至截止日每个实际 A 股运行日的预检、全部 Practice 槽及其决策账本、开盘/尾盘退出、盘后权益和评估都成功，30 笔交易或 3 个完整自然月的样本门才可进入运营复核；无可信日历缓存时保守退回周一至周五。满三个月但不足 30 笔只检查频率和运行。最终高胜率且正收益声明还必须有至少 30 笔交易，同时通过冻结历史胜率参考、交易级 Wilson 95% 下界、首次入场日期×行业的唯一簇数和 Herfindahl 有效簇数、簇等权胜率及其 95% 下界、费用后收益质量、纯牛牛账户归因、正组合收益、最大回撤不超过 6%、收益/回撤不低于 1 及运行/机会完整性；同日同业的批量交易只计一个唯一簇。归因缺失为 `data_quality_blocked`，运行日缺失为 `operations_blocked`。代码或锁定配置变化后报告同样停止晋级，必须归档旧报告/锁并从新的 `DASHBOARD_NIUONE_FORWARD_COHORT_START` 重新累计。要让模拟账户完整走通“协议预检—定时选股—盘面总结评价—决策—自动离场—净值快照—前向归因”，Dashboard 与定时调度器都必须持续运行。v18 还从首次 BUY 起记录持仓阶段路径、在每次主线扫描时更新，并由真实 SELL 冻结退出阶段；缺少任一实际运行日观察或入口/退出阶段对不上路径时，生命周期不能进入人工复核。

v20 漏斗中的实际 BUY 以耐久成交账本为准，并与决策 payload 的执行副本交叉核对；不一致时不能进入人工复核。牛牛首次建仓按北京时间交易日跨 Practice 决策轮次累计，每日最多 2 只；加仓和其他策略不占该额度，历史组合回测使用同一共享规则。

牛牛 BUY 若已通过其他硬检查、只是模型股数超过正数风险许可整手上限，v18 会把实际股数向下裁到该上限，并同时保存模型请求、实际成交、上限和裁单标记。风险上限为零、资格/容量/输入失败仍拒单；这只减少安全订单因轻微报量偏差而整单丢失，不提高任何仓位或风险上限。

牛牛模型 SELL 若请求股数高于正数整手 T+1 可卖量，v18 会按可卖量成交，并保存模型请求、当时可卖量、实际成交和裁单标记供盘后校验。需要裁单但可卖量为零或非整手时仍拒单；本地自动退出和其他战法不改变。

牛牛试仓的日线 V 型恢复比在 v18 中固定为 `[0.60, 2.00)`；评分与成交前复核共用该边界。协议锁会记录上下界；v20 严格前向历史参考胜率随新生产候选冻结为 59.71%。

v18 还冻结主升质量边界：牛牛领涨必须同时满足主线内前 20% 和题材当日强度不低于 60；牛牛启动只接受跨日延续的 `emerging`，已确认 `mainline` 必须改走领涨。评分与成交前复核共用这些失败关闭规则。

v18 还冻结酝酿试仓延续门：题材强势股数不少于 6，或酝酿状态连续至少 3 个交易日；每日最多 2 个合格试仓，单票绝对仓位上限 6.25%，但每笔权益风险预算维持 0.35%/0.30%/0.25%。

v20 把 6.25% 限定为酝酿试仓上限。原试仓或启动仓浮盈处于 2%～12%、生命周期仍处主升且个股仍在强势领涨梯队时，跨日延续的启动主线可在下一交易时段向 10% 上限加仓一次，主线完全确认后再向 20% 上限加仓一次；实际仓位仍受风险预算、主题/组合容量、现金和阶段上限约束。浮盈超过 12% 不追，高潮、分歧、退幕不加仓；首次进入高潮且持仓不亏时减仓 1/3 一次，既有分段止盈、成本保护和 2ATR 跟踪继续执行。

v21 在确认领涨后启用无固定次数的波段再平衡：距本轮收盘高点回落 1 ATR，或连续 3 个交易日未创新高且回落不少于 0.25 ATR，先减仓 1/3；随后只有从减仓价重新上行 0.5 ATR、生命周期回到主升且个股恢复强势领涨，才补回风险预算允许的目标仓位。每次补仓重置周期，必须等待下一次独立回撤才能再次减仓/加仓。分歧可减仓但未修复前不补仓，高潮和退幕也禁止补仓。独立部署的严格前向锁同步为 `niuone-strict-forward-v21`，不能继续沿用旧协议队列。

v22 修复多概念股票的动作/阶段错配：每个牛牛动作按自身生命周期选择相容概念，已确认分支不再因未进入页面主/次两个主题而被排除，`diverging` 中仍保持前 20% 强势核心的股票可继续走领涨；分歧阶段不再重复要求题材当日强度 60。组合容量、价格形态和结构风控保持不变。严格前向锁升级为 `niuone-strict-forward-v22`，默认从 `2026-08-04` 建立新队列；部署前须归档旧协议锁和报告，不能混算 v21 与 v22 成交。

v23 为主升阶段的跨日 `emerging` 行业龙一增加“主升动量试仓”子路由，要求强度不低于 90、评分不低于 8.0、市场非防守且次日高开不超过 3%。该子路由允许 3.2ATR 的价格扩张和 18%/3ATR 的结构止损，但首仓绝对仓位上限固定为 3%，风险预算会随有效亏损距离继续压缩仓位；普通启动、反转和领涨规则不变。严格前向锁升级为 `niuone-strict-forward-v23`，不能混算 v22 与 v23 成交。

v24 将主升动量试仓拆成两种几何：普通入口要求评分不低于 8.1、题材分不低于 70、距 EMA20 不超过 1ATR；极强加速入口允许 2.5～3.2ATR，但必须单日涨幅不低于 9.5%、量比不高于 1.2。质量门通过后首仓绝对上限为 4%，仍由有效亏损距离和组合风险预算继续压缩。严格前向锁升级为 `niuone-strict-forward-v24`，不能混算 v23 与 v24 成交。

管理员回测 v25 固定使用进取参数，页面不再提供均衡/进取选择。服务端会将旧客户端传入的任何档位归一化为 `aggressive`，并忽略旧均衡结果。该调整只升级回测协议为 `niuone-backtest-v25`，生产严格前向协议仍为 v24。

v25 对已经完成高潮减仓、个股仍强且题材分不低于 55、未进入退幕/失活的余仓启用条件化主升跟随：相对龙头排名退出由连续 2 日延长为 3 日确认，跟踪线从 2ATR 放宽为 3ATR。健康条件一旦失效即恢复原两日/2ATR，结构止损、成本保护、主线转弱、退幕和市场硬停止保持不变。独立部署的严格前向锁升级为 `niuone-strict-forward-v25`，管理员回测协议升级为 `niuone-backtest-v26`，不能沿用旧结果。

v26 允许牛牛在防守状态按最低风险档开仓：成熟路径的单笔/组合/主题风险上限为 0.30%/0.90%/0.60%，总仓/主题敞口上限为 20%/12%；试仓进一步收紧为 0.15% 单笔风险、0.30% 主题风险和 5% 主题敞口，并在 0.75R 先减仓 50%。其他资格与执行门槛保持不变，复合风险硬停止仍禁止新仓。独立部署的严格前向锁升级为 `niuone-strict-forward-v26`，管理员回测协议升级为 `niuone-backtest-v27`，不能沿用旧结果。

v27 将东方财富 `f100` 事实行业与牛牛动作选中的 `f103` 题材分开保存。多概念股票使用 75% 当前共振证据和 25% 前序快照历史先验，单股全部题材归因权重归一化为 1；首次建仓冻结入场题材，持仓只有在另一有效题材连续 2 个交易日领先至少 10 分时才切换当前题材。题材风险容量按动作/当前题材统计，Dashboard 分别显示题材和行业。独立部署的严格前向锁升级为 `niuone-strict-forward-v27`，按入场日期×入场题材聚类并要求完整题材归因；管理员回测协议升级为 `niuone-backtest-v28`。部署前必须归档旧锁和报告，不能沿用旧结果。

v29 把 `f103` 作为候选标签而不是最终炒作结论。系统只按排除自身后的同题材共振、同群方向和排名形成个股题材归因，题材识别不发起消息检索，消息摘要也不改变候选、归因或题材总分。系统允许保留未归因质量，再使用归因权重重算题材强股、广度、成交额和领涨股；今日广度按有效样本向全市场广度收缩，Dashboard 折叠同一核心群驱动的标签副本。独立题材页扫描直接跳过消息预检；普通策略扫描仍可将其用于候选股买入前风险检查。题材上下文升级为 v10，旧 v9 快照不提供跨日确认。独立部署的严格前向锁升级为 `niuone-strict-forward-v29`，管理员回测协议升级为 `niuone-backtest-v30`；部署前必须归档旧锁、报告和回测结果。

v30 新增 20 日市场中性化收益波形归因，用目标股票与排除自身后的题材中位超额收益相关性区分多个 `f103` 候选，并按候选内相对排名收缩。牛牛所有扫描模式都不再执行消息预检或大模型调用。题材上下文/专用缓存为 v11/v9，独立部署严格前向/回测协议为 `niuone-strict-forward-v30`/`niuone-backtest-v31`。

v31 修复多概念股票在龙头资格和排序中的重复稀释：15% 权重线继续过滤普通弱分支，单股归因分最高且不低于 60 的首要题材保留唯一低份额例外；结构/今日龙头分别按原始强度/当日涨幅排序，归因分只作同值次序。题材广度、资金、集中度和全部交易风控仍使用原规则。题材上下文/专用缓存为 v12/v10，独立部署严格前向/回测协议为 `niuone-strict-forward-v31`/`niuone-backtest-v32`；部署前必须归档旧协议锁、报告和回测结果。

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

### 更新源码部署

设置页和首页的版本检查只提示 Docker Hub 是否存在更高的严格 SemVer 版本，不会自动拉取代码、替换镜像或重启服务。升级前先备份 `.local-data/`；如果源码目录没有需要保留或处理的未提交冲突，可执行：

```bash
git pull --ff-only
./run.sh --service --no-browser
```

重复运行 `--service` 会更新并重启三个原生服务，同时保留 `.local-data/` 中的配置、数据库和日志。已经安装长期运行服务时，普通执行 `./run.sh`（Windows 为 `run.bat`）也会自动重启托管进程，避免新前端由旧后端提供。尚未安装长期运行服务的前台运行方式使用：

```bash
git pull --ff-only
./run.sh --no-browser
```

启动器会在虚拟环境新建或 `requirements.txt` 哈希变化时安装 Python 依赖，并在前端源码、样式或锁文件变化时重新构建 Vue。`--skip-install` 只跳过 Python 依赖安装检查，不会跳过缺失或过期的前端构建。容器升级请固定新的 `NIUONE_IMAGE` 版本标签，再执行 `docker compose pull` 和 `docker compose up -d --no-build`；完整备份、验证和回滚步骤见[部署、验证和回滚手册](OPERATIONS.md)。

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
| macOS | `~/Library/LaunchAgents/ai.niuone.*.plist` | 当前用户登录后启动，异常退出后自动重启 | `.local-data/runtime/logs/ai.niuone.*.stdout.log` 与 `*.stderr.log` |
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
