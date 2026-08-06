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

公网部署继续运行 `./run-dashboard.sh`：FastAPI/Uvicorn 在 `8787` 同时提供 Vue 公开页面、受管理员密码保护的 `/admin` 和全部 API，不存在第二个生产端口。服务端每 15 秒生成内容寻址快照，浏览器只检查轻量版本指针，并仅在区块变化时取数。完整缓存和反向代理策略见 [Dashboard 增量展示与部署](DASHBOARD_V2.md)。

`/healthz` 只表示 Web 进程存活，适合作为容器 liveness；`/readyz` 同时检查运行目录可写和当前策略所需的市场数据，首次启动初始化期间会返回 `503`，就绪后返回 `200`。`/api/system/data-readiness` 始终返回 `200` 和同一份结构化诊断，供页面展示初始化进度、缓存覆盖率、持久化卷与时区提醒。不要把 `/readyz` 用作会在初始化期间反复重启容器的 liveness 探针。

设置页末尾的“关于”分组展示项目作者、GitHub 仓库、Apache License 2.0、当前版本和 Docker Hub 最新发行版本，并可点击“检查更新”跳过服务端缓存、主动重新查询。“开启自动检测新版本”默认启用并在运行时生效，也可在 `dashboard.env` 中设置 `DASHBOARD_AUTO_VERSION_CHECK_ENABLED=0` 关闭。更新弹窗的“此版本不再提醒”仅保存在当前浏览器；手动点击首页版本号仍可复查，且更高版本发布后会重新提醒。

## 3. 模型配置

NiuOne 需要大模型驱动完整工作流。X 关注列表监控使用具备 `x_search` 能力的 Grok；美股机构评级日报可使用独立的实时网页搜索模型，留空时复用 Grok；A 股盘面总结增强可使用任意兼容 `/chat/completions` 的模型；A 股候选股及龙虎榜连板/连榜股票的消息面预检使用独立配置、具备实时搜索能力的模型；选股后的买卖决策可配置兼容模型，推荐使用 DeepSeek。

核心配置项：

| 场景 | 配置项 |
|---|---|
| 牛牛美股总开关 | `DASHBOARD_US_FEATURES_ENABLED` |
| X 关注列表独立开关 | `X_WATCHLIST_ENABLED`；关闭时不影响美股评级日报 |
| Grok API | `DASHBOARD_GROK_BASE_URL`、`DASHBOARD_GROK_API_KEY`、`DASHBOARD_GROK_MODEL`、`DASHBOARD_GROK_API_MODE`、`DASHBOARD_GROK_CONTEXT_LENGTH` |
| A 股盘面模型总结单独覆盖 | `A_SHARE_MODEL_SUMMARY_BASE_URL`、`A_SHARE_MODEL_SUMMARY_API_KEY`、`A_SHARE_MODEL_SUMMARY_MODEL`、`A_SHARE_MODEL_SUMMARY_MAX_TOKENS` |
| 消息面预检 API | `DASHBOARD_NEWS_BASE_URL`、`DASHBOARD_NEWS_API_KEY`、`DASHBOARD_NEWS_MODEL`、`DASHBOARD_NEWS_API_MODE`、`DASHBOARD_NEWS_MAX_TOKENS`、`DASHBOARD_NEWS_CONCURRENCY` |
| 问财内置数据源 | `IWENCAI_ENABLED`、`IWENCAI_BASE_URL`、`IWENCAI_API_KEY`、`IWENCAI_TIMEOUT_SECONDS`、`IWENCAI_MAX_RETRIES`、`IWENCAI_MAX_CONCURRENCY`、`IWENCAI_CACHE_TTL_SECONDS`、`IWENCAI_DRAGON_TIGER_CRON` |
| 买卖决策 API | `DASHBOARD_DECISION_BASE_URL`、`DASHBOARD_DECISION_API_KEY`、`DASHBOARD_DECISION_MODEL` |
| 买卖决策情报包 | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`、`DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`、`DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |
| 买卖决策交易纪律 | `DASHBOARD_TRADE_DISCIPLINE_TEXT`；为空时使用内置默认纪律，填写后进入模型 prompt 的“必须遵守”段 |
| 模拟账户节奏与仓位参考 | `DASHBOARD_MAX_OPEN_POSITIONS`、`DASHBOARD_MAX_NEW_BUYS_PER_DECISION`、`DASHBOARD_MAX_SINGLE_POSITION_PCT`、`DASHBOARD_MAX_TOTAL_POSITION_PCT`、`DASHBOARD_MIN_CASH_RESERVE_PCT`；默认作为模型参考，Z 哥和板块潮汐等注册硬限制策略会在模拟执行层取全局与策略限制的更严格值 |
| 美股评级单独覆盖 | `US_RATING_MODEL`、`US_RATING_BASE_URL`、`US_RATING_API_KEY`、`US_RATING_MAX_TOKENS` |
| X 关注列表单独覆盖 | `X_WATCHLIST_BASE_URL`、`X_WATCHLIST_API_KEY`、`X_WATCHLIST_MODEL`、`X_WATCHLIST_MAX_TOKENS` |

完成管理员认证后，优先通过页面上的设置按钮进入设置页维护。所有需要模型和 API Key 的分组都提供“测试模型连接”按钮；测试使用页面当前填写值但不会自动保存，API Key 输入框留空时会复用已保存密钥。推文监控和美股评级相关设置由“开启牛牛美股”总开关控制；关闭时设置页会隐藏这些项，后台 X 监控和美股评级定时任务也会跳过。只需停止推文查询并保留美股评级时，可关闭“开启 X 关注列表监控”；守护进程和直接执行入口都会跳过 X 请求，美股评级计划不受影响。也可以直接编辑 `.local-data/dashboard.env`，保存后按配置影响范围重启或等待下一轮任务读取。
`DASHBOARD_GROK_API_MODE` 可设为 `auto`、`responses` 或 `chat`。默认 `auto` 会为 Grok 4.5 使用带 `web_search`/`x_search` 工具的 Responses API，其他模型保持 Chat Completions；兼容网关可显式选择对应模式。`X_WATCHLIST_REQUEST_TIMEOUT_SECONDS` 控制 X 单账号请求超时，默认 `45` 秒。
`DASHBOARD_NEWS_API_MODE` 同样可设为 `auto`、`responses` 或 `chat`。默认 `auto` 会为 Grok 4.5 和 GPT-5 系列搜索模型使用带 `web_search` 工具的 Responses API；Grok Responses 预检模型还会加入 `x_search`，其他模型通过 `web_search` 检索可公开索引的雪球/X 页面，不会改用 `DASHBOARD_GROK_*`。
`*_CONTEXT_LENGTH` 仅表示模型上下文窗口，默认 `128000`；`*_MAX_TOKENS` 表示期望的最大输出长度，调用层会按接口映射为 `max_tokens` 或 `max_output_tokens`。已知不接受 Responses 输出长度参数的 GPT-5.6 网关别名会省略该参数，其他网关若明确返回不支持也会自动去参重试一次。模型响应同时兼容 JSON 和 SSE，即使网关在 `stream=false` 时仍强制返回 SSE。
消息面预检默认最多并发检查 5 只候选股；如果上游出现限流或 403/429，可将 `DASHBOARD_NEWS_CONCURRENCY` 降为 `2` 或 `1`。旧快照中的 `unclassified_response` 若能从已保存摘要唯一判断“利好/利空/中性”，补检流程会在本地修复标签并保留原查询时间，不新增模型请求；仍有歧义时保持未识别。

问财数据源默认关闭。“问财数据源”设置分组提供“测试问财接口”按钮，会使用页面当前地址和密钥发送一次轻量只读查询，不保存配置或改写龙虎榜快照。启用并配置 API Key 后，Dashboard 提供固定用途的
`/api/iwencai/dragon-tiger?date=YYYY-MM-DD&page=1&limit=100` 龙虎榜接口；接口不接受任意自然语言问句，
单页最多 100 只股票，并复用 Dashboard 限流和缓存。返回结果按股票代码去重，`sector` 提供所属行业，`limit_up_reason` 和 `limit_up_reason_category` 分别提供问财归纳的涨停原因及原因类别，重复榜单记录保留在 `details` 中。每日快照会与前一 A 股交易日的滚动快照比较；同一股票连续出现时写入 `consecutive_listed`、`consecutive_list_days` 和最多 10 个 `consecutive_list_dates`，缺失相邻快照时安全重置，不跨数据缺口推测。消息面预检批次使用 `IWENCAI_DRAGON_TIGER_CRON` 配置的龙虎榜计划查询时间作为起始时间，不使用上游响应的 `generated_at`；龙虎榜成功返回后，快照中尚未检索且满足连板（`limit_up_streak >= 2`）或连续上榜（`consecutive_listed = true` 且 `consecutive_list_days >= 2`）任一条件的股票使用 `DASHBOARD_NEWS_*` 配置查询最近 3 天消息，最多保持 5 路并发。公告、交易所披露和主流财经媒体用于事实核验，雪球与 X/Twitter 仅作为单独的市场舆情，不得把未核实帖子写成公司事实。每只股票的 `news_precheck.checked` 以及汇总字段 `limit_up_news_checked_codes`、`limit_up_news_pending_codes` 会持久化；旧的 `continuous_news_*` 字段作为兼容别名保留。调度器启动时会追补最近应有的交易日快照，每次新拉取前也先补检当前快照，因此周五遗漏可在周末重启或下次拉取前完成。全部完成后，后续同日拉取不会重复调用模型。模型未配置或失败不影响主榜快照，也不会回退到 `DASHBOARD_GROK_*`。`seats` 保留买卖前五的机构专用、普通营业部及问财明确标注的游资/量化席位，并记录同一营业部可能同时出现的 `buy_rank`、`sell_rank` 和金额；`institution_seats` 继续提供机构子集以兼容现有消费者。
问财响应属于研究数据快照，发生超时、计数不一致或上游失败时会返回明确状态，不会覆盖账户、成交或其他真实交易记录。Dashboard 的 `/dragon-tiger` 栏目可按交易日实时查询；当日数据以及下一次成功查询前仍在滚动快照中的最近数据无需密码，更早日期必须输入管理员密码并建立有效会话。当日实时回源为空时，接口继续返回最近成功快照，避免零点后在新榜单生成前把页面替换为空状态。所有非当日响应均不进入公共或 CDN 缓存，确保新数据覆盖后旧日期立即恢复保护。只有与最新快照日期一致的请求会在回源前直接复用本地数据，其他日期不持久化。Cron 默认在 A 股交易日北京时间
18:00 更新 `.local-data/runtime/cron/output/iwencai_dragon_tiger_latest.json`。该文件只保留最近一次非空成功查询，下一次成功查询会原子覆盖它，并清理旧版本生成的 `iwencai_dragon_tiger/YYYY-MM-DD.json` 归档；空结果或主榜失败继续保留上一份有效快照。席位明细失败不会阻断股票榜单；查询日期未变化时，当前快照中的有效席位记录不会被缺失结果覆盖。

买卖决策情报包默认开启。每次实战选股扫描后的模型决策都会读取盘面监控、隔夜美股、指数行情、板块涨跌、行业资金、热门股、候选消息面和账户仓位摘要，并把压缩后的 `decision_intelligence` 写入模拟交易决策日志。行情源失败时会保留 `source_status`，本轮决策继续按可用信息和既有风控执行。

实战页面的规范地址为 `/practice`，候选查询与刷新接口分别为 `/api/practice_candidates` 和 `/api/practice_candidates/refresh`。基于 `?category=practice` 或 `?category=b1_screen` 的旧链接及 `/api/b1_screen` 接口仅作为兼容入口保留。

### 3.1 行情与资金流设置

设置页的“行情与资金流设置”集中维护指数行情与行业资金流参数：

| 配置 | 默认值 | 可选范围 | 生效方式 |
|---|---:|---:|---|
| `DASHBOARD_INDICES_TTL_SECONDS` | `60` | 大于 0 秒 | 运行时热应用 |
| `DASHBOARD_NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED` | `1` | `0` 或 `1` | 重启 Dashboard 后生效 |
| `DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS` | `30` | `30`～`600` 秒 | 重启 Dashboard 后生效 |
| `DASHBOARD_INDUSTRY_FLOW_PLAYBACK_SPEED` | `0.5` | `0.5`、`0.75`、`1`、`1.5`、`2`、`5`、`10` | 运行时热应用；资金流页面下一次加载生效 |
| `DASHBOARD_INDUSTRY_FLOW_SIDE_LIMIT` | `10` | 每侧 `1`～`10` 个行业 | 运行时热应用；下一次资金流请求生效 |
| `DASHBOARD_INDUSTRY_FLOW_SAMPLE_INTERVAL_SECONDS` | `60` | `60`～`600` 秒 | 运行时热应用；后台下一轮采样生效 |
| `DASHBOARD_INDUSTRY_FLOW_MORNING_START` | `09:25` | 北京时间 `HH:MM` | 运行时热应用；后台下一轮判断生效 |
| `DASHBOARD_INDUSTRY_FLOW_MORNING_END` | `11:31` | 北京时间 `HH:MM` | 运行时热应用；后台下一轮判断生效 |
| `DASHBOARD_INDUSTRY_FLOW_AFTERNOON_START` | `13:00` | 北京时间 `HH:MM` | 运行时热应用；后台下一轮判断生效 |
| `DASHBOARD_INDUSTRY_FLOW_AFTERNOON_END` | `15:01` | 北京时间 `HH:MM` | 运行时热应用；后台下一轮判断生效 |

行业资金流默认只在 A 股交易日北京时间 09:25～11:31、13:00～15:01 采样，可在设置页分别修改四个边界时间。保存时必须满足“上午开始 < 上午结束 < 下午开始 < 下午结束”。调整采样窗口或间隔不会删除已经保存的真实采样点；窗口外的历史点不参与当前动画，新采样按更新后的窗口和最小时间间隔追加。

指数行情页的“主力资金流向”和资金流动页共享东方财富行业板块接口的“今日主力净额”口径（字段 `f62`，单位由元换算为亿元），并共用同一份 60 秒缓存。新版快照和采样历史分别保存为 `industry_main_money_flow_cache.json`、`industry_main_flow_history.json`。旧版总流入减总流出口径的缓存与历史文件会保留，但不会与主力净额动画混合播放。

指数行情页的 A 股市场情绪曲线默认每 30 秒读取一次腾讯证券沪深 A 股全市场快照，并用行情返回的现价、最高价、涨停价和跌停价计算涨停板、跌停板与炸板数量；红盘、绿盘按行情涨跌幅正负统计。同一次通过完整性校验的逐股最新报价还会交给题材强度快速计算器，不会为题材页面重复请求腾讯。快速计算器只读取私有日 K 与行业映射缓存，沿用最近完整研究扫描的龙虎榜确认分量且不读取消息数据，并把最新行情时间和计算完成时间分别写入题材快照；覆盖不足、时间戳过期或计算失败时保留上一份有效题材结果。该行为由 `DASHBOARD_NIUONE_MAINLINE_MINUTE_REFRESH_ENABLED` 控制，共享采样间隔由 `DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS` 控制。页面下方的实际量能优先使用东方财富上证指数与深证成指当日 1 分钟成交额合计；该请求失败或滞后时，回退到同一批腾讯全市场行情的累计成交额，并在接口和页面标明实际来源。预测全天量能采用分段口径：09:30～09:34 只使用 09:25 已撮合完成的全市场竞价成交额作为今日实时输入，以最近最多 20 个有效配对交易日的全天成交额中位数为基线，按“历史全天成交额中位数 ×（今日竞价成交额 ÷ 历史竞价成交额中位数）^0.5”估算，避免样本外的极端竞价额被按 1:1 弹性放大；盘中实际累计量能不参与这五分钟的预测，竞价有效配对少于 10 日时只保留实际量能。09:35 起改用最近 20 个完整交易日的 5 分钟累计成交分布，以当前实际累计量能除以相同时点累计占比的中位数估算全天量能；完整样本不足 20 日时同样不生成替代预测。竞价任务只保存 09:27 前且覆盖不少于 4,000 只股票的结构化样本，开盘后补跑数据不会混入。预测增量为“预测全天量能 − 最近一个完整上一交易日的全天成交额”，允许为负；比较基准独立于当前预测模型的训练样本，同一交易日所有有效增量点使用同一基准日。历史文件在当天完整市场宽度样本之外，只精简保留最近一个交易日的实际累计量能曲线；30 秒采样最多保留 600 个聚合点，足以覆盖完整交易日。接口按相同交易进度对齐“今日实际量能”和“前日同期量能”，并计算“较昨日同期差”（今日实际量能减去前一交易日同进度量能），正值表示放量、负值表示缩量。所有量能曲线单位均为亿元。接口同时返回当前阶段采用的数据源、样本区间、样本数和 5 分钟间隔（如适用）。统计口径包含 ST，不含 B 股、北交所及无有效现价证券。后台只在 A 股交易日 09:30～11:30、13:00～15:00 采样，真实点保存在 `market_breadth_history.json`；旧样本缺少成交额或增量字段时原样保留并显示为空缺，不补写零值。若同一天包含不同预测模型的历史点，接口仅隐藏与最新模型不兼容的预测和增量字段，红绿盘、涨跌停及实际量能等真实记录仍然保留。腾讯分片不完整、成交额覆盖不足或请求失败时保留上一份有效历史，不写入伪零值。

盘中 20 日成交分布模型首次成功构建后，会原子保存到私有运行目录的 `cron/output/turnover_profile_cache.json`。Dashboard 重启时只恢复缓存键与当前交易日一致、模型版本匹配且通过完整性校验的模型，然后继续用最新实际累计量能重新计算预测；跨日、损坏或不完整缓存不会被复用。上游刷新失败不会覆盖已有有效模型缓存。

行业资金流快照、资金流采样和市场情绪曲线以北京时间 09:00 作为展示日切点：前一自然日的收盘数据在零点后继续展示至次日 08:59:59，09:00 起清空当日展示并等待新的有效采样。市场情绪历史在展示日完整采样之外，额外保留最近一个交易日的精简实际量能曲线。Dashboard 启动时会校验文件日期，常驻后台任务在每日北京时间 09:00 原子清空 `industry_main_money_flow_cache.json`，并按样本时间滚动 `industry_main_flow_history.json`：只移除非当前展示日的样本，顶层日期过期或文件中混有跨日样本都不会删除有效的当日记录。每次行业资金成功采样时还会先原子更新 `industry_main_flow_history.recovery.json` 恢复副本；重启时主文件缺失、损坏或意外变空，会从副本合并恢复当日真实样本。市场宽度历史同步滚动 `market_breadth_history.json`，移除上一展示日红绿盘、涨跌停等情绪字段，仅归档其实际累计量能。相关 API 内存缓存会同步失效。09:00 后若上游仍返回前一日时间戳，服务端会拒绝重新展示或写入该快照，页面保持空状态直到取得当日首个有效采样。

### 3.2 实战策略调度与进程归属

严格前向 v18 把牛牛新仓容量与组合回测对齐：每个北京时间交易日跨 Practice 决策轮次累计最多首次建仓 2 只。执行层从持久化成交状态重建当天已开仓代码并按代码幂等去重；加仓和其他策略的新仓不占牛牛额度，达到上限后的新标的以 `position_capacity` 拒绝。该数值和计数规则随协议一并冻结。

实战策略没有各自独立的选股定时任务。Dashboard 默认在交易日 09:10 启动全量非 ST 股票日 K 预热，把最近 120 根腾讯前复权日线保存到私有 SQLite；冷部署、卷丢失或日期过期时，不再等待 09:10 窗口，而会在服务启动后立即有界初始化。中断后的同日重试只补缺失股票，成功序列不会被失败结果覆盖。默认覆盖率达到 90% 才允许实战扫描；Dashboard 启动的扫描只读取日期有效的本地历史并合并批量实时行情，不在交互任务内逐股回源。覆盖不足时，手动任务排队等待初始化并在页面展示阶段、完成数和失败数；定时任务明确记为数据未就绪，不会使用不完整数据进入模拟交易。

Dashboard 内置的 B1 调度器在每个计划时间先使用实时指数、行业涨跌、行业主力资金、市场宽度/量能与已有盘面扫描生成统一的“此刻盘面总结与评价”，再启动共享扫描器。腾讯全市场实时报价阶段另有 90 秒默认总预算，避免单个上游慢响应耗尽整个 480 秒扫描预算。扫描器读取 `DASHBOARD_ACTIVE_STRATEGY`，只运行当前策略套件的评分器；扫描结束后，定时流程一方面把同一份总结与评价传入模型判断和模拟执行层复核，另一方面在后台启动独立的全市场题材强度研究扫描。后者忽略 `DASHBOARD_ACTIVE_STRATEGY`，只更新题材专用缓存，不参与候选或买卖。同一运行目录下的多个 Dashboard 实例通过进程租约互斥预热和完整扫描，防止重复扫描与重复交易；手动任务终态会原子持久化，服务重启只把未完成任务标记为中断，不自动重放交易。

实战页不再用 B1 涨跌家数的独立阈值规则生成另一个“盘面评价”。总结产物的 `tone` / `tone_label` 同时作为页面评价和交易上下文风险级别；模型不可用时调用同一模块的本地汇总规则。手动点击“生成此刻盘面总结与评价”或“手动运行选股与交易策略”会刷新该产物；定时运行则复用 `DASHBOARD_PRACTICE_SCHEDULE_TIMES`。生成失败时保留当日上一份有效总结和评价，不用不完整快照覆盖。

| 配置 | 默认值 | 影响范围 | 生效方式 |
|---|---|---|---|
| `DASHBOARD_ACTIVE_STRATEGY` | `niuone` | 当前新候选、模型 Prompt 和新买入规则 | 运行时热应用；下一轮扫描生效 |
| `DASHBOARD_B1_SCHEDULE_ENABLED` | `1` | 是否启动 Dashboard 内置选股调度线程 | 需要重启 Dashboard |
| `DASHBOARD_PRACTICE_SCHEDULE_TIMES` | `09:25,10:00,10:30,11:00,11:20,13:00,13:30,14:00,14:30,14:50` | 实战盘面总结评价、当前策略选股及买卖决策时间点 | 运行时热应用；旧键 `DASHBOARD_B1_SCHEDULE_TIMES` 仅作兼容读取 |
| `DASHBOARD_B1_SCHEDULE_CATCHUP_MINUTES` | `35` | Dashboard 短暂离线后的漏触发补跑窗口 | 需要重启 Dashboard |
| `DASHBOARD_B1_SCAN_TIMEOUT_SECONDS` | `480` | 一轮完整选股进程的硬超时；超时时返回当前阶段而非统一错误 | 需要重启 Dashboard |
| `DASHBOARD_TENCENT_QUOTE_STAGE_TIMEOUT_SECONDS` | `90` | 腾讯全市场批量实时报价阶段的总预算，允许 15～300 秒 | 需要重启 Dashboard |
| `DASHBOARD_KLINE_CACHE_ENABLED` | `1` | 盘中扫描是否优先读取并增量回填本地日 K SQLite | 需要重启 Dashboard |
| `DASHBOARD_KLINE_PREWARM_ENABLED` | `1` | 是否启动盘前全市场日 K 预热线程 | 需要重启 Dashboard |
| `DASHBOARD_KLINE_PREWARM_TIME` | `09:10` | A 股交易日盘前预热时间 | 需要重启 Dashboard |
| `DASHBOARD_KLINE_PREWARM_WORKERS` | `12` | 盘前下载并发数，服务端硬上限为 16 | 需要重启 Dashboard |
| `DASHBOARD_KLINE_PREWARM_TIMEOUT_SECONDS` | `600` | 单次盘前预热总超时 | 需要重启 Dashboard |
| `DASHBOARD_KLINE_PREWARM_CATCHUP_MINUTES` | `15` | Dashboard 短暂离线后的预热补跑窗口 | 需要重启 Dashboard |
| `DASHBOARD_KLINE_BOOTSTRAP_ENABLED` | `1` | 冷启动或缓存过期时是否立即初始化，不受盘前窗口限制 | 需要重启 Dashboard |
| `DASHBOARD_KLINE_BOOTSTRAP_MAX_ATTEMPTS` | `3` | 每个日期的自动初始化最大尝试次数 | 需要重启 Dashboard |
| `DASHBOARD_KLINE_READINESS_MIN_COVERAGE_PERCENT` | `90` | 实战扫描放行所需的日期有效日 K 覆盖率，允许 90～100 | 需要重启 Dashboard |
| `DASHBOARD_MANUAL_DATA_INITIALIZATION_TIMEOUT_SECONDS` | `660` | 手动任务排队等待市场数据就绪的总时限 | 需要重启 Dashboard |
| `DASHBOARD_B3_EXIT_TIME` | `09:37` | 开盘自动离场检查 | 后续 Cron 周期读取 |
| `DASHBOARD_TIME_EXIT_TIME` | `14:45` | 尾盘自动离场和时间窗检查 | 后续 Cron 周期读取 |
| `DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON` | `5 9 * * 1-5` | 在首轮实战决策前冻结或校验严格前向协议 | Scheduler 启动时立即运行，之后在周一至周五按 Cron 复检 |
| `DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON` | `15 15 * * 1-5` | 无交易副作用地刷新行情并保存盘后账户权益 | 后续 Cron 周期读取；必须早于严格前向评估 |
| `DASHBOARD_NIUONE_FORWARD_CRON` | `20 15 * * 1-5` | 从完整模拟成交账本生成牛牛严格前向报告 | 周一至周五的后续 Cron 周期读取 |
| `DASHBOARD_NIUONE_FORWARD_COHORT_START` | `2026-08-04` | 严格前向队列纳入首次 BUY 的起始交易日 | 后续 Cron 周期读取；改值后必须建立新协议锁 |

旧部署若只配置了 `DASHBOARD_B1_SCHEDULE_TIMES`，Dashboard 会继续读取原值；新旧键同时存在时以 `DASHBOARD_PRACTICE_SCHEDULE_TIMES` 为准。设置页只展示新键，下一次保存该时间列表时会写入新键并删除本地 `dashboard.env` 中的旧键。

09:25 扫描处于开盘竞价结束后的静默期。系统可以生成候选和模型动作，但不会直接按竞价参考价记成交；需要执行的动作会排队，09:30 后由 Dashboard 的延迟决策线程重新检查交易时段、最新价格、现金和策略风险预算。

用户可在实战页面点击“手动触发选股及买卖策略”运行完整链路。该操作与定时流程使用同一扫描器、策略配置和执行层，不是绕过风控的强制成交入口。页面普通刷新仅读取缓存与账户状态。

每轮 B1 定时或手动决策都会先刷新全部已有持仓，并按各持仓保存的 `strategy_mark` 检查原策略退出规则；当前激活策略只控制新候选和 BUY。候选为零或日内亏损预算触发时，SELL/HOLD 检查仍会继续，日内亏损预算只暂停新开仓。

本地自动退出也由独立 Cron Scheduler 进程在专用时间点调用。结构止损、板块潮汐退潮、策略时间窗、2R 和 2ATR 等仍是离散检查，不是实时逐笔监控；要覆盖完整生命周期，Dashboard 和 Cron Scheduler 两个进程都必须运行。

牛牛严格前向证据从 `2026-08-04` 起累计。Cron Scheduler 每次启动都会立即运行 `--protocol-only`，不打开或要求交易数据库；工作日 09:05 默认再复检一次，因此正常启动或 09:05 后、09:25 前的迟启动都能在首轮计划决策前冻结/校验 `cron/state/niuone_forward_protocol.json`。起始日前的预检同时冻结不含股票代码的零持仓账户基线；基线缺失、带仓或迟于起始日时，账户收益不可归因。预检只重试一次，确定性指纹不一致不会用 5 分钟重试阻塞其他定时任务。模拟成交除保留最近 200 条 JSON 展示日志外，还会把完整成交 payload 幂等写入 `niuniu.db`；首次 BUY 的主线/排名/行业/成交跳空快照以及信号生成时间、计划槽、计划/补跑/手动来源、direct/deferred 模式和定仓边界，加仓和部分退出因此不会因展示日志裁剪而丢失。协议 v18 还为每轮决策保存完整展示机会集、规范化策略标识、是否进入决策池、模型请求/最大许可股数及结构化过滤/拒单原因；交易仍只使用显式 `trade_items`，不会因审计字段扩大候选池。延迟成交记录继承原计划槽机会集；盘后报告按计划槽去重并按五阶段输出 observed→eligible→model BUY→executed BUY 漏斗、定仓利用率及一致性异常。完整空候选池有效，字段残缺、重复代码/排名或资格与过滤原因矛盾的证据无效。Cron Scheduler 默认在实际 A 股运行日 15:15 通过 `--snapshot-equity` 只刷新行情并保存收盘后账户权益，15:20 再只读合并 SQLite 历史和最近 JSON，原子更新私有文件 `cron/output/niuone_forward_evaluation.json`；JSON 状态层独有行只作恢复覆盖，不能代替耐久 payload 或权益点。v18 还要求牛牛首次 BUY 初始化持仓阶段路径，后续每次主线扫描追加或扩展阶段段落，实际 SELL 冻结退出阶段；完成生命周期若缺少任一实际运行日的阶段观察，或入口、路径、退出时间/阶段不一致，同样保持 `data_quality_blocked`。

协议锁冻结队列起始日、门槛、影子候选、牛牛评分/选择/退出/执行、调度和耐久成交/决策存储相关源码，以及非密钥运行配置的 SHA-256 指纹；运行配置包括预检/盘后 Cron、耐久数据库/恢复状态和两份运行审计状态的有效路径，只保存逐项摘要，不把路径、Prompt、模型地址等原文写入报告。`--as-of` 只控制报告截止日，锁的冻结时间和起始日前重冻资格始终取实际墙钟日期，不能在队列开始后用回填日期覆盖旧锁。后续指纹不一致时，服务保留原锁、盘前任务返回非零状态，盘后报告标记为 `protocol_mismatch` 并禁止晋级，即使交易数或时间门槛已经满足也不例外。Scheduler 在 `niuone_cron_scheduler.json` 有界保留 400 日任务终态，每个任务每日最多 10 次；Dashboard 在 `b1_schedule_state.json` 有界保留 400 日 Practice 槽终态。Practice 槽只有选股与买卖决策链成功且完整决策证据已写入 SQLite 才记为 `ok`；模型或落盘失败记 `error`，只有缓存而无法证明决策已执行记 `skipped`。自动退出的成交或系统决策落盘失败同样使独立任务失败。

报告只在同一协议下达到 30 笔完整零仓到零仓交易或满 3 个自然月、全部已完成生命周期的入口归因字段 100% 完整，并且队列起始日至报告截止日的全部实际 A 股运行日覆盖率为 100% 时，才标记为可人工复核，绝不自动晋级策略。运行日优先按已有交易所日历缓存确定，无可信缓存时保守按周一至周五处理。每个完整运行日必须同时具备：首个决策槽前成功预检、全部 `DASHBOARD_PRACTICE_SCHEDULE_TIMES` 槽为 `ok`、每个槽都有带完整候选证据的 SQLite 决策行、开盘退出检查成功、尾盘退出检查成功、15:15 盘后净值快照成功、15:20 前向评估成功。样本门满足但运行证据缺失时状态为 `operations_blocked`；缺失历史机会或净值不能通过事后补一份报告还原，应归档旧队列并从新起始日重新累计。旧 payload、无耐久 payload、缺少主线状态/行业、同阶段排名、信号/调度时点、计划槽、执行模式或定仓边界的成交仍保留在描述性统计与缺失字段诊断中；样本门已满足但归因不完整时状态为 `data_quality_blocked`。满 3 个月只允许检查“为何没有形成足够交易”，`review_scope=frequency_and_operations_only`。生命周期成效门要求至少 30 笔完整可归因交易、点胜率不低于冻结的 59.71% 历史参考、交易级 Wilson 95% 胜率下界高于 50%、费用后平均净收益和累计已实现盈亏为正、利润因子高于 1；还要按首次入场日期×行业形成至少 30 个唯一簇和 30 个 Herfindahl 有效簇，簇等权胜率不低于 59.71%、其正态 95% 下界高于 50%，且簇等权平均净收益为正。最终高胜率且正收益声明还要求队列内没有非牛牛/未知策略成交，每个运行日都有 15:00 后耐久权益点，`equity = cash + market_value` 且初始资金连续，组合收益为正、最大回撤不超过 6%、收益/回撤不低于 1，并同时通过运行与机会漏斗质量门。同日同业的批量交易只增加一个唯一簇，集中样本不能用笔数掩盖。单纯漏跑盘后报告不会丢失成交账本，但会留下不可假定为完整的运行日或净值缺口。确需修改生产规则、锁定配置或重启无效队列时，应先停止 Dashboard 与 Cron Scheduler，归档现有报告和协议锁，把 `DASHBOARD_NIUONE_FORWARD_COHORT_START` 改为新规则启用的交易日，并在该日期之前启动预检；新队列首次运行会生成新锁。不得只移走协议锁而沿用旧起始日，否则会把旧协议交易纳入新队列。

v20 漏斗中的实际 BUY 读取耐久成交账本，并与决策 payload 的执行副本交叉核对；两边不一致会显示独立诊断并保持 `data_quality_blocked`，不能被决策副本掩盖。

v18 对牛牛 BUY 启用受限风险裁单：100 股整手模型请求若仅超过一个正数的确定性最大许可股数，则按最大许可股数成交；模型请求、实际股数、最大许可股数和裁单标记都进入耐久证据。该规则不放宽候选资格、每日/持仓/主题容量、结构止损输入、现金储备或风险预算，上限为零仍拒单；板块潮汐和其他战法继续使用原执行语义。

v18 也修复模型直接卖出牛牛持仓时的 T+1 机会损失：若 100 股整手请求只因包含当日锁定仓位而高于正数整手可卖量，执行层按可卖量成交，而不再整单变成零成交。模型请求、当时可卖量、实际股数和裁单标记写入耐久成交，盘后报告单独汇总并校验；需要裁单但可卖量为零或非整手时仍拒单。系统结构止损、分批止盈等自动退出以及其他战法不使用这条例外。

v18 同时把牛牛试仓的日线 V 型恢复比冻结为 `[0.60, 2.00)`。评分器与成交前复核都会拒绝恢复不足六成或已经达到两倍的试仓候选；后者应等待启动、领涨或转强等成熟动作，而不是继续占用试仓名额。协议锁显式保存上下界；v20 将新生产候选的历史参考胜率冻结为 59.71%。

v18 还把主升质量写入协议身份：牛牛领涨必须同时满足主线内前 20% 和题材当日强度不低于 60；牛牛启动只接受跨日延续的 `emerging`，已确认 `mainline` 必须改走领涨。评分和成交前复核调用同一失败关闭规则。

v18 同时冻结酝酿试仓延续门与资金利用边界：题材强势股数必须不少于 6，或酝酿状态至少连续 3 个交易日；每日最多保留 2 个合格试仓，单票绝对仓位上限为 6.25%。进攻/轮动/修复的单笔风险预算仍为 0.35%/0.30%/0.25%，不会因为提高绝对上限而放宽连续风险约束。

v20 在此基础上增加两级阶段加减仓：6.25% 只是酝酿试仓上限；原试仓或启动仓浮盈处于 2%～12%、仍处主升且仍为强势领涨梯队时，跨日延续的启动主线先在下一交易时段按成熟风险预算向 10% 上限加仓一次，主线完全确认后再向 20% 上限加仓一次。实际目标仍取风险定仓、主题/组合风险、现金与阶段上限的最小值；浮盈超过 12% 不追仓，高潮、分歧、退幕均不得加仓。首次进入高潮且持仓不亏时只执行一次减仓 1/3，原有分段止盈、成本保护与 2ATR 跟踪不取消。

v21 将确认领涨后的固定次数限制替换为可重复的减仓/补仓周期：从本轮收盘高点回落 1 ATR，或连续 3 个交易日未创新高且回落至少 0.25 ATR，减仓 1/3 并记录一次已执行的风险释放；只有随后从减仓价重新上行 0.5 ATR、生命周期回到主升且个股恢复强势领涨，才允许补回动态风险上限。补仓会清除武装状态并重开一轮，下一次必须由新的独立回撤重新武装，终身次数字段固定为 `null`。分歧可触发减仓，但未修复分歧、高潮和退幕禁止补仓。生产规则变更使严格前向协议升级为 `niuone-strict-forward-v21`；旧协议队列不能与 v21 成交混算。

v22 修复多概念股票的动作/阶段错配：每个牛牛动作按自身生命周期选择相容概念，已确认分支不再因未进入页面主/次两个主题而被排除，`diverging` 中仍保持前 20% 强势核心的股票可继续走领涨；分歧阶段不再重复要求题材当日强度 60。每日新仓、总持仓、同主题持仓、结构止损和价格形态门槛不变。严格前向锁升级为 `niuone-strict-forward-v22`，默认从 `2026-08-04` 新队列开始；部署前须归档旧协议锁和报告，不能把 v21 与 v22 成交混算。

v23 增加条件化“主升动量试仓”：仅当生命周期为主升、题材仍为跨日延续的 `emerging`、股票为行业龙一且强度不低于 90、评分不低于 8.0、市场非防守时，才允许在无普通突破/收复买点的情况下试仓。该路由的价格扩张上限为 3.2ATR，结构止损上限为 18%/3ATR，次日执行高开上限为 3%；首仓绝对仓位上限固定为 3%，实际仓位继续按有效亏损距离和账户风险预算取更小值。其他牛牛路径保持原门槛。严格前向锁升级为 `niuone-strict-forward-v23`，不得与 v22 成交混算。

v24 根据 2026 年 1～6 月因果回放收紧主升动量试仓：普通入口要求评分不低于 8.1、题材分不低于 70 且距 EMA20 不超过 1ATR；2.5～3.2ATR 仅保留单日涨幅不低于 9.5%、量比不高于 1.2 的极强加速例外。通过质量门后首仓绝对上限提高到 4%，但有效亏损距离、账户/题材风险预算和次日高开 3% 上限继续取更小值。严格前向锁升级为 `niuone-strict-forward-v24`，不得与 v23 成交混算。

管理员回测 v25 取消均衡/进取可选档位，牛牛回测始终强制使用进取参数：账户风险预算放大 1.35 倍、总仓/题材敞口放大 1.15 倍，容量为 3/6/3。服务端会忽略旧客户端传入的风险档位，旧均衡结果不恢复。这只升级 `niuone-backtest-v25` 回测协议；生产严格前向规则仍为 v24。

v25 修正高潮减仓后余仓被相对排名过早清空的问题。仅当持仓已经执行高潮减仓、仍是强势股、题材分不低于 55 且状态不是退幕/失活时，跌出龙头梯队由连续 2 个交易日改为 3 个交易日确认，跟踪止盈由 2ATR 放宽为 3ATR；一旦上述健康条件失效，立即回到原两日确认和 2ATR，并继续先执行结构止损、成本保护、主线转弱、退幕和市场硬停止。严格前向锁升级为 `niuone-strict-forward-v25`，管理员回测协议升级为 `niuone-backtest-v26`，两者均不得与旧协议结果混算。

v26 允许牛牛在防守状态按最低风险档开仓：成熟路径的单笔/组合/主题风险上限为 0.30%/0.90%/0.60%，总仓/主题敞口上限为 20%/12%；试仓进一步收紧为 0.15% 单笔风险、0.30% 主题风险和 5% 主题敞口，并在 0.75R 先减仓 50%。生命周期、龙头、形态、结构止损、涨停和组合容量门槛不变；复合风险硬停止仍禁止新仓。严格前向锁升级为 `niuone-strict-forward-v26`，管理员回测协议升级为 `niuone-backtest-v27`，不得混用旧协议证据。

v27 拆分牛牛的事实行业和交易题材：东方财富 `f100` 继续写入 `industry/sector`，动作选中的 `f103` 概念写入 `signal_theme`。多概念股票按当前题材强度、题材内排名、同题材共振和当日排名形成 75% 当前证据，并把前序快照累积为 25% 历史先验；单股全部题材归因权重之和固定为 1。首次建仓冻结 `entry_theme` 及归因来源，持仓的 `active_theme` 只有在另一有效题材连续 2 个交易日领先至少 10 分时才切换，行业与入场题材均不被后续扫描静默改写。风险容量改按动作/有效题材统计；严格前向锁升级为 `niuone-strict-forward-v27`，绩效簇改为首次入场日期×入场题材，并把题材、来源、分数、权重和历史先验列为必需证据。管理员回测协议升级为 `niuone-backtest-v28`；部署前归档旧锁和报告，不得混算旧协议成交。

v29 把多概念归因前置到题材聚合：`f103` 只提供候选标签，当前证据不再读取包含个股自身贡献的题材总分，而由排除自身的同题材共振、同群方向、当日排名和结构排名组成，再叠加 25% 前序先验。题材识别链路不发起消息面检索，消息摘要不会新增候选、修改归因分或题材总分；独立题材页扫描直接跳过消息预检，普通策略扫描只可将其用于候选股买入前风险检查。多概念通过 softmax 分配并保留未归因质量；题材强股、成交额、广度、今日强度和领涨股全部按归因权重重算，今日广度还按有效样本向全市场广度收缩。归因权重不足 15% 的股票不能成为对应题材领涨股，公开 Top 5 折叠高度重叠标签。题材上下文升级为 v10，旧 v9 快照不参与跨日确认；严格前向锁升级为 `niuone-strict-forward-v29`，管理员回测协议升级为 `niuone-backtest-v30`。部署前归档旧锁、报告和回测结果，不得混算。

v30 使用 20 日市场中性化收益波形增强多概念归因：目标股票与排除自身后的题材中位超额收益逐日相关，并按该股票全部 `f103` 候选中的相对排名收缩。牛牛所有扫描模式均跳过消息预检和大模型调用；消息配置只保留给其他明确使用消息预检的模块。题材上下文/专用缓存升级为 v11/v9，严格前向/管理员回测升级为 `niuone-strict-forward-v30`/`niuone-backtest-v31`，旧结果不得混算。

v31 修复多概念股票在龙头环节被归因权重重复降级：15% 权重线继续过滤普通弱分支，但单股归因分最高且不低于 60 的首要题材可保留龙头资格；随后结构龙头按原始强度、今日龙头按当日涨幅排序，不再乘以归因权重。管理员回测按真实次日开盘校验结构资格，5bp 模拟滑点只影响成交价和风险定仓。题材广度、资金、集中度、生命周期、价格形态和全部风险门槛不变。题材上下文/专用缓存升级为 v12/v10，严格前向/管理员回测升级为 `niuone-strict-forward-v31`/`niuone-backtest-v32`；部署前归档旧锁、报告和回测结果，不得混算。

排查“策略没有触发”时依次检查：

1. `.local-data/dashboard.env` 中 `DASHBOARD_ACTIVE_STRATEGY` 是否为预期套件；
2. `DASHBOARD_B1_SCHEDULE_ENABLED` 是否开启，Dashboard 进程是否仍在运行；
3. 当前时间是否进入 `DASHBOARD_PRACTICE_SCHEDULE_TIMES` 的时间点或补跑窗口；
4. `.local-data/runtime/cron/state/b1_schedule_state.json` 中对应时间槽是 `ok`、`error` 还是 `skipped`；
5. `.local-data/runtime/market_data/tencent_daily_klines.sqlite3` 是否存在且当日 `prewarm_runs` 状态为 `completed`；
6. `.local-data/runtime/cron/output/multi_strategy_latest.json` 是否包含最新 `generated_at`、当前策略候选和所需上下文字段；
7. 自动退出未运行时，确认 Cron Scheduler 进程及 `.local-data/runtime/logs/niuone_cron_scheduler.log`。
8. 严格前向报告未更新时，先确认 Scheduler 启动日志中的协议预检与 `DASHBOARD_NIUONE_FORWARD_PREFLIGHT_CRON`，再检查 `DASHBOARD_NIUONE_EQUITY_SNAPSHOT_CRON`、`DASHBOARD_NIUONE_FORWARD_CRON`、`niuniu.db` 和 `.local-data/runtime/cron/output/niuone_forward_evaluation.json`；`operations_blocked` 同时检查 `niuone_cron_scheduler.json`、`b1_schedule_state.json` 中报告列出的缺失日期/事件，`portfolio_evidence_blocked` 检查报告中的账户基线、缺失权益日期和结构化错误字段，`protocol_mismatch` 只核对 `changed_fields` 字段名并按新队列流程处理。不要覆盖原锁，也不要把这些私有文件复制到公开排障材料。

板块潮汐的用户规则、风险预算和开发者数据契约见[策略研究说明](strategies/README.md#34-板块潮汐)。

## 4. 验证流程

```bash
./scripts/validate.sh
```

验证内容：

1. Python 语法检查
2. Vue/Vite 生产构建和前端 JavaScript 语法检查
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

该脚本会构建 `web/` Vue 应用，并检查 `web/` JavaScript、`app/` Python、Shell 与 Windows BAT 入口及完整单元测试。

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
