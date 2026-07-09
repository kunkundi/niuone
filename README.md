# NiuOne · 牛牛1号

<p align="left">
    <a href="https://github.com/kunkundi/niuone/actions/workflows/ci.yml"><img src="https://github.com/kunkundi/niuone/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License" /></a>
    <a href="https://linux.do" alt="LINUX DO"><img src="https://shorturl.at/ggSqS" /></a>
</p>

NiuOne 是一个一站式市场研究工作台，面向个人研究、策略观察和模拟交易场景，整合 A 股行情面板、策略筛选、X 关注列表监控、美股机构评级摘要与定时归档，方便集中查看、跟踪和复盘市场信息。

## 在线演示
<https://niuone.cn>

> NiuOne 仅用于研究、信息整理和个人决策辅助，不构成任何投资建议。

## 功能概览

- **开箱即用**：在 macOS、Windows、Linux 上一键启动，浏览器自动打开本地工作台。
- **市场总览**：集中查看 A 股指数、板块、热门股票、资金流、市场流向、消息历史和策略结果。
- **策略研究**：内置基础策略、Z 哥和李大霄策略，也支持用自然语言写自己的预设策略。
- **模拟交易与复盘**：使用牛牛实战模拟账户跟踪持仓、收益曲线和买卖决策记录。
- **关注与归档**：整理 X 关注列表监控、美股机构评级摘要和定时任务输出，方便后续回看。
- **设置与保护**：通过设置按钮管理模型、功能开关和访问保护；个人本机默认直接可用，需要时可设置密码。

## 系统要求

用于本地体验和部署：

| 依赖 | 用途 |
|---|---|
| Python 3.11+ | 运行本地服务、创建虚拟环境、执行任务脚本 |
| Git | 克隆项目 |
| 可访问 PyPI 的网络 | 首次运行时安装 Python 依赖 |
| 浏览器 | 打开本地工作台 |

用于开发和验证：

| 依赖 | 用途 |
|---|---|
| Node.js 18+ | `./scripts/validate.sh` 会用 `node --check` 检查 dashboard 内嵌 JavaScript |

Python 依赖由一键启动脚本自动安装，当前核心依赖见 [requirements.txt](requirements.txt)。

## 从零部署结论

从 Git 拉取一个全新的项目后，只要机器具备上面的本地运行依赖，按“快速开始”可以启动本机 dashboard。首次启动会自动创建虚拟环境、安装依赖、生成 `.local-data/dashboard.env`，并监听 `http://127.0.0.1:8787/`。

需要注意三点：

- 本地页面、设置页、模拟账户和基础视图可以先打开体验。
- 完整智能分析工作流需要在设置页配置模型服务商、API Key 和相关开关；未配置模型时，X 关注列表监控、美股机构评级摘要、A 股消息面预检和买卖决策不会完整运行。
- 管理员密码默认为空；需要保护设置页时，可以在启动时设置，也可以启动后到设置页里配置。

## 快速开始

```bash
git clone https://github.com/kunkundi/niuone.git
cd niuone
```

| 系统 | 启动方式 |
|---|---|
| macOS | 终端执行 `./run.sh` |
| Windows | 双击或 CMD 执行 `run.bat` |
| Linux | 终端执行 `./run.sh` |

Linux 如果提示没有执行权限：

```bash
chmod +x run.sh
```

启动后浏览器会自动打开：

```text
http://127.0.0.1:8787/
```

首次运行会自动完成：

- 创建 `.local-data/`
- 创建 `.local-data/.venv`
- 安装 `requirements.txt`
- 生成 `.local-data/dashboard.env`
- 将数据库、本地凭据、日志、任务输出写入 `.local-data/runtime/`

如果不想自动打开浏览器：

```bash
./run.sh --no-browser
```

Windows：

```cmd
run.bat --no-browser
```

常用启动参数：

| 参数 | 说明 |
|---|---|
| `--admin-password VALUE` | 启动前写入管理员密码到 `.local-data/dashboard.env` |
| `--port VALUE` | 启动前写入 dashboard 端口到 `.local-data/dashboard.env` |
| `--no-browser` | 启动后不自动打开浏览器 |
| `--skip-install` | 跳过依赖安装检查 |

启动前设置管理员密码：

```bash
./run.sh --admin-password "change-this-to-a-strong-password"
```

Windows：

```cmd
run.bat --admin-password "change-this-to-a-strong-password"
```

指定 dashboard 端口：

```bash
./run.sh --port 8877
```

Windows：

```cmd
run.bat --port 8877
```

常用环境变量：

| 环境变量 | 说明 |
|---|---|
| `NIUONE_LOCAL_DATA_DIR` | 修改私有运行目录，默认是项目内 `.local-data` |
| `DASHBOARD_AUTH_ENABLED` | 访问认证开关；本地首次运行默认 `0` |

## 配置

首次启动会自动生成本地配置，并把它保存在 `.local-data/` 中。日常使用不需要手动编辑配置文件；启动成功后点击页面上的设置按钮，即可完成运行配置、模型配置、选股策略和访问控制管理。

本机开箱即用时，NiuOne 默认监听 `127.0.0.1:8787`，访问认证关闭，管理员密码为空。需要保护设置页时，可以点击设置按钮完成管理员保护配置。

完整功能依赖外部模型和行情/信息源网络。没有配置模型密钥时，dashboard 仍可打开，但以下流程会受限：X 关注列表监控、美股机构评级日报、A 股盘面模型总结、候选股消息面预检、选股后的买卖决策。

### 首次设置建议

| 设置页区域 | 建议 |
|---|---|
| 管理员保护 | 为设置页设置一个强密码，避免运行配置和模型密钥被他人修改。 |
| 访问控制 | 按需开启认证，限制未授权访问。 |
| 模型配置 | 填入需要使用的模型服务商和密钥；未配置模型时，部分智能分析工作流不会完整运行。 |
| 选股策略 | 先使用默认内置策略体验；熟悉后再切换到预设文字策略。 |
| 功能开关 | 按需开启 X 关注列表监控、美股机构评级日报和 A 股消息面预检。 |

模型使用上，X 关注列表监控和美股机构评级日报推荐使用 Grok，并由“开启牛牛美股”开关控制；A 股候选股消息面预检可单独选择具备实时搜索能力的模型；选股后的买卖决策可配置兼容模型，推荐使用 DeepSeek。

### 买卖决策情报包

牛牛实战每次生成买卖决策时，都会把多渠道信息压缩成“全局决策情报包”交给决策模型，并随决策日志保存用于复盘。默认包含：

- 盘面监控指引和隔夜美股盘面总结。
- A 股指数、A50、美股指数/期货、黄金和原油等指数行情。
- 板块涨跌幅、行业资金流入/流出、热门成交额/换手/涨幅榜。
- 候选股最近消息面预检，以及候选行业与板块/资金/热门榜的共振或背离。
- 当前账户现金、总仓、主要持仓仓位、盈亏状态和每只持仓的策略标记。

情报包默认开启，可在设置页“买卖决策模型”中调整 `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`、`DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS` 和 `DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS`。任一行情源临时失败时，本轮决策会记录来源状态并继续执行，不会因为单个信息源缺失而中断。

仓位大小统一按 `股价 × 股数 ÷ 当前总权益` 计算；成交日志会记录本笔买卖占总权益比例，以及买卖后单票和账户总仓位比例。

### 策略大赛上报

需要参加策略大赛时，推荐在设置页第一个“策略大赛”分组中登录比赛服务端。可以使用比赛服务端本地用户名/密码，也可以点击“LinuxDo 登录”通过 LinuxDo Connect 注册或登录牛牛账号；LinuxDo 的 client secret 只保存在比赛服务端，客户端只接收一次性登录 ticket 换回的比赛 `user_token`。服务端发布或更新比赛后，会通过 SSE 事件流通知已连接的客户端；客户端收到通知后刷新可参加比赛列表。点击“参加”后，本地会保存 participant 凭证并把该比赛设为当前官方区间。日常模拟账户仍在本地照常运行；每笔本地成交会同步上传为比赛订单，服务端只把及时到达、签名有效且成交价通过实时行情校验的订单计入官方区间影子账户。

设置页“策略大赛”分组仍保留手工配置方式：可填写比赛服务端 URL、比赛 ID、参赛昵称或已有 participant/secret。显式设置 `DASHBOARD_CONTEST_ENABLED=0` 会关闭比赛上报；通过页面参加比赛时无需手工填写比赛 ID 和密钥。服务端可用 `DASHBOARD_CONTEST_SETTINGS_VISIBLE=0` 控制是否输出该设置分组。

比赛服务端会返回 `official`、`late`、`price_mismatch`、`quote_unverified` 或 `rejected` 等状态；这些状态只影响比赛排行榜，不会回滚本地模拟交易。

### 选股策略

设置页的“选股策略”提供两种模式，一次只激活一种：

| 模式 | 适合场景 |
|---|---|
| 内置策略 | 开箱即用的默认选择。可以在基础策略、Z 哥、李大霄中选择一个，参与 A 股扫描和买卖决策。 |
| 预设文字策略 | 已经有自己的交易想法时使用。直接输入自然语言策略，系统会交给买卖决策模型整理成选股、买入、卖出、仓位和时间纪律。 |

选择内置策略时，系统按当前选中的策略组进行扫描和决策。选择预设文字策略时，内置策略偏好不再参与本轮决策，系统会先生成中性候选池，再让模型按你的文字策略做最终判断；如果预设文字为空，本轮不会新开仓，只会对已有持仓做风控判断。

### 设置页保护

管理员密码用于保护设置页及其管理能力，包括运行配置、模型配置和访问控制管理。它不是普通访问密码，而是进入设置页时的额外保护层。

本地一键启动默认不要求管理员密码，方便第一次打开就能使用。需要保护设置页时，可以点击设置按钮设置管理员密码；如需启动前设置，可参考快速开始里的管理员密码示例。

系统会在本地保存备用管理员凭据，可用于恢复管理员身份。请不要把 `.local-data/` 下的配置、凭据文件，或任何包含模型密钥的截图、日志提交到 Git 或公开 issue。

手动修改 `.local-data/` 下的配置文件后，需要重启服务才会完整生效。更完整的部署、重启和回滚流程见 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

## 运行数据与安全

NiuOne 默认把真实运行数据留在工程目录内的 `.local-data/`，便于本地迁移和备份，也避免把本地数据和敏感信息提交到源码仓库。

| 路径 | 内容 |
|---|---|
| `.local-data/dashboard.env` | 本地运行配置，可能包含模型密钥和管理员密码 |
| `.local-data/.venv/` | 一键启动创建的 Python 虚拟环境 |
| `.local-data/runtime/config.yaml` | 模型服务商配置 |
| `.local-data/runtime/*.db` | 消息、用户、模拟交易等本地数据库 |
| `.local-data/runtime/cron/` | 定时任务状态和输出 |
| `.local-data/runtime/logs/` | dashboard、定时任务和监控日志 |

`.local-data/` 已被 Git 忽略；公开 issue、日志或截图前，请先确认没有带出本地凭据、模型密钥、管理员密码或数据库路径中的隐私信息。

## 项目结构

```text
.
├── app/                    # 本地服务和任务源码
├── tests/                  # 单元测试
├── scripts/                # 验证、迁移和独立任务脚本
├── docs/                   # 操作文档
├── config/                 # 运行策略说明
├── tools/                  # 本地维护工具
├── dashboard.env.example   # 生产式本地配置示例
├── run.sh                  # macOS/Linux 一键启动
├── run.bat                 # Windows BAT 一键启动
├── run-dashboard.sh        # dashboard LaunchAgent/后台服务入口
├── run-niuone-cron-scheduler.sh
├── run-x-watchlist-daemon.sh
└── requirements.txt        # Python 依赖清单
```

## 验证

确认 dashboard 是否启动成功：

```bash
curl -s -o /dev/null -w 'HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/
curl -s -o /dev/null -w 'HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/api/auth/status
```

预期均返回 `HTTP:200`。

修改代码或配置后，可运行项目自带验证脚本。验证脚本需要 Node.js；建议复用一键启动创建的虚拟环境，并使用临时运行目录，避免读取本机 `.local-data/` 里的真实模型配置：

```bash
(
  VALIDATE_DATA_DIR="$(mktemp -d)"
  trap 'rm -r "$VALIDATE_DATA_DIR"' EXIT
  env -i \
    HOME="$HOME" \
    PATH="$PATH" \
    TMPDIR="${TMPDIR:-/tmp}" \
    NIUONE_LOCAL_DATA_DIR="$VALIDATE_DATA_DIR" \
    DASHBOARD_HOME="$VALIDATE_DATA_DIR/runtime" \
    DASHBOARD_ENV_FILE="$VALIDATE_DATA_DIR/dashboard.env" \
    PYTHON_BIN="$PWD/.local-data/.venv/bin/python" \
    ./scripts/validate.sh
)
```

验证脚本会检查 Python 语法、dashboard 内嵌 JavaScript、Shell 启动脚本、Windows BAT 入口，并运行 `tests/` 单元测试。更完整的部署、重启、日志检查和回滚流程见 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

## 常见问题

### `python3 is required but was not found in PATH`

安装 Python 3.11 或更高版本，并确认 `python3 --version` 能在终端输出版本号。Windows 上脚本会依次尝试 `python`、`py -3` 和 `python3`。

### 依赖安装失败或下载很慢

首次启动需要从 PyPI 安装依赖。确认网络可访问 PyPI 后重新执行启动脚本；如果所在网络需要镜像源，可先按本机 pip 配置镜像，再重新运行 `./run.sh` 或 `run.bat`。

### 端口 `8787` 已被占用

换一个端口启动：

```bash
./run.sh --port 8877 --no-browser
```

Windows：

```cmd
run.bat --port 8877 --no-browser
```

### 页面能打开但智能分析没有结果

先进入设置页配置模型服务商、API Key、功能开关和任务时间。部分 A 股数据、交易日判断和行情摘要还依赖外部数据源可用性，以及当前是否处于合适的交易时段。

### 想做不污染真实数据的烟测

macOS/Linux 可以使用临时运行目录和临时端口：

```bash
NIUONE_LOCAL_DATA_DIR="$(mktemp -d)" ./run.sh --port 8877 --no-browser
```

## 内置战法与策略来源

NiuOne 的选股策略由“策略来源”和“内置战法”两层组成。默认使用内置策略来源；也可以在设置页切换到预设文字策略，让买卖决策模型按用户输入的自然语言策略生成本轮执行规则。

内置策略下，基础策略、Z 哥和李大霄是同级概念，一次只启用一个；卖出风控归属于 Z 哥体系，不作为独立策略组。预设文字策略下，基础策略只作为中性候选池，最终规则由买卖决策模型根据用户预设文字生成。

### 内置策略组

内置策略用于给扫描器和买卖决策模型提供固定的选股偏好、仓位纪律和退出约束。当前内置三个同级策略组：

| 策略组 | 包含战法/代理信号 | 定位 |
|---|---|---|
| 基础策略 | 突破确认、趋势回踩 | 通用技术候选池 |
| Z 哥 | 少妇B1、B2确认、B3中继、超级B1、Z哥卖出风控 | Z 哥战法体系代理 |
| 李大霄 | 低估蓝筹、底部发育、逆向情绪和去杠杆防守 | 价值与底部防守代理 |

### 基础策略

基础策略和 Z 哥、李大霄处于同一选择层级，用于给扫描器提供通用技术候选：

- **突破确认**：平台或前高突破后回踩站稳，再作为确认仓处理。
- **趋势回踩**：强趋势股回踩BBI/EMA不破，按低吸仓处理。

### Z 哥

NiuOne 的 A 股策略筛选和模拟交易规则中，参考并实现了 zettaranc-skill 中整理的 Z 哥选股战法思想。当前归属于 Z 哥的买入战法包括：

- **少妇B1**：J值低位、N型上移、缩量回调、牛绳/BBI约束，强调试错仓和近止损。
- **B2确认**：B1后放量中/大阳确认趋势，拒绝偏滞后或离BBI过远的追高。
- **B3中继**：B2后小阳/十字星分歧转一致，快进快出，T+1开盘不涨走。
- **超级B1**：放量破位洗盘后缩量企稳，J值仍负，只赌一次，未兑现则离场。

归属于 Z 哥体系的卖出风控包括：买入K线/前低止损、硬止损、防卖飞评分、卤煮半仓、S1/S2/S3逃顶、出货五式、白线/BBI破位、峰值回撤/ATR吊灯保护，以及 B3、B2、超级B1 的时间离场纪律。

### 李大霄

李大霄策略参考 li-daxiao-skill 的“政策、价值、底部发育、逆向情绪、杠杆风控”框架，用主板高流动性蓝筹、低位企稳、低换手、缩量低波动、反追高和反“黑五类”作为可执行代理信号。

策略元数据集中在 `app/strategy_registry.py`。新增内置策略时，优先在注册表里增加策略组及其 `label/color/desc/scorer/profile/position_limit_pct/aliases`，再在 `app/multi_strategy_screen.py` 中实现对应 `score_xxx(rows)` 评分函数。扫描器会自动遍历当前策略组里的 scorer，并把 `strategy_meta` 输出给 dashboard 和模拟交易模块。

本项目仅在本地模拟交易和研究辅助场景中使用这些公开整理的战法规则，不代表原作者背书，也不构成任何投资建议。若继续扩展或二次分发相关策略说明，请同时保留对 zettaranc-skill 与 li-daxiao-skill 的引用。

## 文档

- [docs/STANDALONE.md](docs/STANDALONE.md)：独立运行说明
- [docs/OPERATIONS.md](docs/OPERATIONS.md)：部署、验证和回滚手册
- [config/runtime-policy.md](config/runtime-policy.md)：运行数据和敏感信息处理策略

## License

NiuOne 使用 [Apache License 2.0](LICENSE) 发布。
