<img width="1200" alt="NiuOne 牛牛1号" src="docs/assets/readme/niuone.webp" />

简体中文 | [English](README_EN.md)

<p align="left">
  <a href="https://linux.do"><img src="https://shorturl.at/ggSqS" alt="LINUX DO" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License" /></a>
  <a href="https://github.com/kunkundi/niuone/actions/workflows/ci.yml"><img src="https://github.com/kunkundi/niuone/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://hub.docker.com/r/kunkundi/niuone"><img src="https://img.shields.io/docker/pulls/kunkundi/niuone?label=Docker%20Pulls" alt="Docker Pulls" /></a>
</p>

## 项目简介

NiuOne（牛牛1号）是一套本地优先的市场研究与模拟交易系统，主要面向 A 股，也覆盖隔夜美股信息。它将行情、消息、策略和模拟账户集中到一个 Web 看板中，并可接入大模型辅助整理信息和生成交易判断。

后台任务可以按计划采集竞价、盘中和盘后行情，以及资金流向、板块表现和海外市场信息。启用模型服务后，系统会按照用户设定的策略规则完成消息检索、行情分析和模拟买卖决策。账户状态、交易记录和决策依据都保存在本地，模拟成交可通过飞书、钉钉、企业微信、Telegram 或 Server酱推送。

NiuOne 可以运行在个人电脑或服务器上，配置和研究数据由用户自行管理。系统只操作模拟账户，不连接券商交易接口，也不会触及真实资金。

## 在线演示

<https://niuone.cn>

> 本页面仅用于个人研究、模拟交易和信息展示，不构成证券、期货投资咨询、投资建议、荐股服务或任何买卖依据；不承诺收益，不代客理财，不收取荐股费用。

## 功能预览

点击动图可打开对应的在线页面。

### 盘面监测总览

<p align="center">
  <a href="https://niuone.cn/">
    <img width="1200" alt="盘面监测总览交互演示：联动查看市场情绪与量能、指数、主线机会、候选池、财经快讯和板块资金" src="docs/assets/readme/overview.gif" />
  </a>
</p>

<p align="center"><sub>市场状态、市场宽度、涨跌停、主要行业资金和账户仓位汇聚为决策摘要，并联动展示核心行情与机会。</sub></p>

### 模拟交易与账户复盘

<p align="center">
  <a href="https://niuone.cn/practice">
    <img width="1200" alt="模拟交易交互演示：切换每日和累计收益曲线并打开交易日历" src="docs/assets/readme/practice-trading.gif" />
  </a>
</p>

<p align="center"><sub>账户总览、收益曲线、当前持仓和交易日历集中展示，方便回看每日与累计收益。</sub></p>

### 题材强度雷达

<p align="center">
  <a href="https://niuone.cn/niuone-mainline">
    <img width="1200" alt="题材强度交互演示：对照今日与结构排名、展开领涨和龙头代表股并查看覆盖原因" src="docs/assets/readme/theme-strength.gif" />
  </a>
</p>

<p align="center"><sub>今日强度与跨日结构排名并排展示，并结合东财即时排名、上涨广度和领涨股进行交叉参考。</sub></p>

### 主力资金流入与流出

<p align="center">
  <a href="https://niuone.cn/indices">
    <img width="1200" alt="主力资金流向交互演示：切换 A 股行情、滚动查看主力净流入流出榜，并重播行业资金流动画" src="docs/assets/readme/capital-flow.gif" />
  </a>
</p>

<p align="center"><sub>A 股行情、活跃板块、个股主力净流入 / 净流出和行业资金变化一页呈现。</sub></p>

### 市场情绪与红绿盘家数

<p align="center">
  <a href="https://niuone.cn/indices">
    <img width="1200" alt="市场情绪交互演示：鼠标沿日内曲线移动，查看涨跌停、炸板、红绿盘家数与量能指标" src="docs/assets/readme/market-breadth.gif" />
  </a>
</p>

<p align="center"><sub>日内曲线联动展示涨跌停、炸板、红绿盘家数，以及预测、实际和增量量能。</sub></p>

### 自动化盘面监控

<p align="center">
  <a href="https://niuone.cn/market-monitor">
    <img width="1200" alt="自动化盘面监控交互演示：展开并滚动查看 A 股盘后总结" src="docs/assets/readme/market-monitor.gif" />
  </a>
</p>

<p align="center"><sub>竞价、午盘和盘后报告汇总核心判断、资金流向、热门板块、风险提示与次日关注项。</sub></p>

### 财经快讯

<p align="center">
  <a href="https://niuone.cn/realtime-news">
    <img width="1200" alt="财经快讯交互演示：筛选重要信息、切换新闻来源并滚动浏览 NewsNow 聚合快讯" src="docs/assets/readme/realtime-news.gif" />
  </a>
</p>

<p align="center"><sub>聚合财联社、金十数据与华尔街见闻等可选来源，支持按来源和重要程度筛选，并在上游异常时继续显示本地缓存。</sub></p>

### 本地配置中心

<p align="center">
  <img width="1200" alt="本地配置中心交互演示：浏览配置分组、切换交易策略并打开行情与资金流设置" src="docs/assets/readme/dashboard-settings.gif" />
</p>

<p align="center"><sub>数据源、模型、策略、通知和运行参数都可以在本地设置页中管理。</sub></p>

> 全部动图均为浅色主题下的真实页面交互；其中的行情、新闻与模拟账户数据仅用于界面展示，不构成投资建议。

## 功能概览

- **市场看板**：集中展示题材强度、指数、板块、市场情绪、行业资金流、重要财经快讯、龙虎榜和历史消息。
- **题材与策略研究**：并排呈现今日强度和跨日结构排名，结合全市场行情、题材归因和东财即时排名观察主线。项目内置基础、Z 哥、李大霄、板块潮汐和牛牛战法，也支持用自然语言配置候选、买入、卖出、仓位与时间规则。
- **信息聚合与模型分析**：通过 NewsNow 聚合财联社与金十实时快讯，并整理 A 股竞价、午盘、盘后报告，以及隔夜美股和问财龙虎榜。兼容的大模型服务可用于消息检索、摘要和结构化分析。
- **模拟交易**：覆盖候选筛选、买卖决策、持仓盈亏、收益曲线和交易日志，不连接券商，也不使用真实资金。
- **自动化与通知**：支持定时采集、报告生成、数据入库和后台监控；模拟成交可推送到飞书、钉钉、企业微信、Telegram 和 Server酱。
- **本地数据管理**：配置、数据库、日志和任务输出默认保存在独立运行目录。设置页支持连接测试和版本检查，但不会自动安装更新。

主要页面与依赖：

| 页面 | 能力 | 额外配置 |
|---|---|---|
| `/practice` | 模拟账户、候选、盘面总结、模型决策、收益曲线和交易日历 | 模型决策需配置 `DASHBOARD_DECISION_*` |
| `/niuone-mainline` | 全市场今日/结构题材双榜、跨日主线、有效覆盖、代表股和东财即时校验 | 无密钥；需要行情源可访问 |
| `/indices`、`/industry-flow` | 指数、板块、活跃股票、行业主力资金、市场情绪与量能 | 无密钥；需要行情源可访问 |
| `/dragon-tiger` | 按交易日查看龙虎榜席位、连板/连榜及消息预检 | 需启用并配置问财；消息预检模型可选 |
| `/market-monitor` | A 股竞价/午盘/盘后与隔夜美股摘要 | 需长期运行调度器；模型增强可选 |
| `/realtime-news` | NewsNow 聚合的可选财经新闻来源；总览同步展示 5 条并可仅保留重要信息 | 无需 API Key；Compose 默认随附内置 NewsNow，管理页可搜索并多选财经商业来源 |
| `/admin` | 配置、连接测试、版本与运行状态 | 始终需要管理员认证 |

策略细节见[策略研究说明](docs/strategies/README.md)，代码结构和扩展约定见 [app 模块结构](docs/APP_ARCHITECTURE.md)。

Dashboard 由 Vue 3 + Vite 和 FastAPI/Uvicorn 构建。行情请求、交易决策和记录计算均在服务端完成，前端通过同源增量快照获取数据。架构、缓存和部署说明见 [Dashboard 增量展示与部署](docs/DASHBOARD_V2.md)。

## 系统要求

| 依赖 | 要求 | 用途 |
|---|---|---|
| Python | 3.11+ | 运行服务、任务脚本和本地工具 |
| Node.js | 22.12+ | 构建 Vue 3/Vite 前端；容器运行镜像不需要 |
| pnpm | 11.15.1（启动器可通过 npx 调用） | 安装锁定前端依赖并构建 |
| Git | 推荐最新稳定版 | 获取和更新项目 |
| 浏览器 | Chrome、Edge、Safari、Firefox 等现代浏览器 | 访问本地工作台 |
| 网络 | 首次运行需访问 PyPI 与 npm registry | 安装 Python 和前端依赖 |

## 快速部署

克隆项目：

```bash
git clone https://github.com/kunkundi/niuone.git
cd niuone
```

先为每个需要长期保留数据的实例固定部署方式和数据位置：

| 使用场景 | 推荐方式 | 运行数据位置 |
|---|---|---|
| 长期运行、服务器或希望统一依赖 | Docker Compose | 命名卷 `niuone-data` |
| 单机直接使用、无需容器 | 原生启动器 | `.local-data/`，或 `NIUONE_LOCAL_DATA_DIR` 指定的目录 |
| 开发、调试和验收 | 隔离实例 | 独立 Compose 项目与端口，或 `/tmp/niuone-*` 等临时目录 |

Docker 与原生部署的数据不会自动同步。同一个正式实例不要在两种方式之间交替启动，否则可能看到另一套较旧的账户、配置和交易日历。开发者可以并行运行隔离实例，但必须使用不同端口和不同数据位置。

macOS / Linux：

```bash
./run.sh
```

Linux 如果提示没有执行权限：

```bash
chmod +x run.sh
./run.sh
```

Windows 可双击 `run.bat`，或在 CMD 中执行：

```cmd
run.bat
```

启动完成后访问：

```text
http://127.0.0.1:8787/
```

首次运行会自动：

1. 创建 `.local-data/` 私有运行目录；
2. 创建 `.local-data/.venv/` Python 虚拟环境；
3. 安装 `requirements.txt` 中的依赖；
4. 按 `web/pnpm-lock.yaml` 安装并构建 Vue 前端；
5. 生成 `.local-data/dashboard.env`；
6. 初始化运行目录并启动 FastAPI dashboard。

### 常用启动参数

| 参数 | 说明 |
|---|---|
| `--port VALUE` | 设置并保存 dashboard 端口 |
| `--no-browser` | 启动后不自动打开浏览器 |
| `--skip-install` | 跳过 Python 依赖安装检查；前端缺失或过期时仍会构建 |
| `--service` | 注册并启动当前平台的长期运行服务 |

例如，使用 `8877` 端口且不自动打开浏览器：

```bash
./run.sh --port 8877 --no-browser
```

Windows：

```cmd
run.bat --port 8877 --no-browser
```

看板首页和展示数据保持公开访问；设置页与管理 API 始终需要管理员认证。首次启动时，请使用服务自动生成的 bootstrap 管理密钥进入设置页；本地路径为 `$DASHBOARD_HOME/dashboard_admin_token.txt`，默认即 `.local-data/runtime/dashboard_admin_token.txt`。登录后可在“访问控制”中设置管理员密码，新密码会立即生效并注销旧会话。也可在启动前直接编辑权限为 `0600` 的 `.local-data/dashboard.env`，设置 `DASHBOARD_ADMIN_PASSWORD`；不要通过命令行参数传递密码，以免进入 shell 历史或进程列表。

如需将运行数据保存在其他位置，可设置：

```bash
NIUONE_LOCAL_DATA_DIR=/path/to/private-data ./run.sh
```

## 容器化部署

项目提供单一牛牛1号镜像和 Compose 编排。Compose 会启动 dashboard、定时调度器以及官方 NewsNow 实例。牛牛1号配置、数据库、日志和任务输出保存在 `niuone-data` volume，NewsNow 数据单独保存在 `newsnow-data` volume。

从源码构建并启动：

```bash
./scripts/docker-build.sh
docker compose up -d --no-build
docker compose ps
```

Windows PowerShell 使用 `powershell -ExecutionPolicy Bypass -File .\scripts\docker-build.ps1` 完成构建，再执行 `docker compose up -d --no-build`。构建脚本只会在构建前后清理带 NiuOne 镜像标签且已悬空的旧镜像；不会执行全局 Docker 构建缓存清理，也不会删除其他项目镜像、容器或数据卷。

### Docker 日常启动与重启

电脑或容器引擎重启后，先确认 Docker Engine 已运行，再从原部署目录恢复服务：

```bash
docker compose up -d
```

仅需重启现有容器时，可重启全部服务，或只重启牛牛1号的 Dashboard 与调度器：

```bash
docker compose restart
docker compose restart dashboard scheduler
```

`docker compose restart` 不会应用 Compose、环境变量、端口或镜像变更。修改这类配置后使用 `docker compose up -d`；修改源码后需要重建镜像。使用 Docker Hub 镜像的部署则先拉取再重建容器：

```bash
# 源码构建
./scripts/docker-build.sh
docker compose up -d --no-build

# Docker Hub 镜像
docker compose pull
docker compose up -d --no-build
```

每次启动或重启后检查：

```bash
docker compose ps
docker compose port dashboard 8787
curl -s -o /dev/null -w 'healthz=%{http_code}\n' http://127.0.0.1:8787/healthz
curl -s -o /dev/null -w 'readyz=%{http_code}\n' http://127.0.0.1:8787/readyz
```

`dashboard` 应显示为 `healthy`，`healthz` 应返回 `200`；首次初始化期间 `readyz` 可能暂时返回 `503`，数据就绪后应变为 `200`。跨平台的端口占用检查和故障诊断见[独立运行说明](docs/STANDALONE.md#docker-服务启动重启与数据卷)。

Compose 已设置 `restart: unless-stopped`，但容器引擎本身仍需启动。Docker Desktop 用户可启用登录时自动启动，Linux 管理员应确认 Docker 服务已设置为开机启动。

Compose 项目名会参与物理卷名的生成，其来源可能是当前目录名、`-p` 或 `COMPOSE_PROJECT_NAME`。首次部署后应保持项目名和原部署目录稳定；更改它们可能创建一套新的空卷，让页面看起来像历史数据丢失。可用 `docker compose ls` 和 `docker compose ps` 确认当前项目。

日常重启、升级和排障不要使用 `docker compose down -v` 或 `docker volume rm`。文档中的 `niuone-data` 是 Compose 逻辑卷名，实际名称通常带项目名前缀；删除该卷会同时删除模拟账户、配置和历史记录。普通的 `docker compose down`、`restart` 和 `up -d` 都会保留命名卷。

默认在宿主机 `127.0.0.1:8787` 提供服务；公开页面和受密码保护的 `/admin` 管理页使用同一端口。NewsNow 只在 Compose 内网的 `newsnow:4444` 监听，不额外暴露宿主机端口；Dashboard 会自动使用它。查看日志或停止服务：

```bash
docker compose logs -f
docker compose down
```

`docker compose down` 会同时停止 NewsNow 并保留两个数据卷。用户无需启动 NewsNow、填写服务地址或维护额外配置；后续再次启动牛牛1号时会自动恢复。运维人员如需锁定上游版本，可选设置 `NEWSNOW_IMAGE`。

从 Docker Hub 部署指定版本：

```bash
export NIUONE_IMAGE=kunkundi/niuone:v0.0.12
docker compose pull
docker compose up -d --no-build
```

如需修改宿主机端口，可设置 `NIUONE_PORT`。只有在已经配置反向代理、HTTPS 和独立访问控制时，才应将监听地址改为 `0.0.0.0`：

```bash
NIUONE_BIND_ADDRESS=0.0.0.0 NIUONE_PORT=8877 docker compose up -d
```

> 看板首页保持公开访问，设置页与管理 API 始终需要管理员认证。容器使用 `/data/dashboard.env` 中配置的 `DASHBOARD_ADMIN_PASSWORD`；未配置时，请执行 `docker compose exec dashboard cat /data/runtime/dashboard_admin_token.txt` 读取 bootstrap 管理密钥。运行配置与密钥保存在 volume 中，不会打入镜像。

## 首次配置

基础页面无需模型密钥即可启动。信息检索、智能摘要和部分自动化流程需要额外配置外部服务。

启动后通过页面中的设置入口完成配置；先使用配置的管理员密码或 bootstrap 管理密钥完成认证。原生部署把配置写入 `.local-data/`，Docker 部署写入 `niuone-data` volume；两者不会自动同步，均无需修改源码。建议首次使用时依次完成：

1. 设置需要启用的数据源与自动化任务；
2. 按需配置兼容的模型服务地址、模型名称和 API Key；
3. 如需成交提醒，在“交易通知”中开启总开关，再从下拉框添加所需渠道并填写对应配置；Telegram 需填写 Bot Token 与 Chat ID，Server酱需填写 SendKey；
4. 妥善保存或轮换管理员凭据；
5. 重启服务，使所有需要重启的配置生效。

### 交易通知配置

NiuOne 支持将模拟买入和卖出成交推送到飞书、钉钉、企业微信、Telegram 和 Server酱。通知只在成交状态成功落盘后发送；同一轮的多笔成交会合并为一条消息，并明确标注“模拟成交，非实盘”。单个渠道发送失败不会回滚成交，也不会影响其他渠道。

#### 在设置页添加渠道

1. 进入“设置 → 交易通知”，将“启用模拟成交通知”设为“启用”。
2. “单次推送超时秒数”默认是 `5` 秒，可设置为 `1`–`30` 秒。
3. 在“通知渠道”下拉框中选择渠道，点击“添加渠道”。
4. 填写该渠道卡片中的必填字段，按需填写签名密钥；使用卡片右上角的状态开关决定该渠道是否接收成交通知，开关旁会明确显示“已启用”或“已关闭”。
5. 点击卡片底部的“发送测试通知”验证配置。测试成功后保存业务配置，再按需添加其他渠道；保存后通知配置会热生效，无需为通知配置单独重启服务。

“关闭”只停止该渠道的成交通知，不会删除 Webhook、Bot Token、Chat ID、SendKey 或签名密钥，之后可直接重新启用。点击渠道卡片右上角的“移除”会关闭并收起该渠道；点击“保存本组设置”后，NiuOne 才会删除该渠道已经保存的全部配置，再次添加时状态为“未设置”。如果在保存前重新添加渠道，原配置不会被删除。对于仍处于添加状态的渠道，敏感字段留空保存表示保留旧值。

“发送测试通知”只向当前卡片对应的一个渠道发送，不受通知总开关或渠道开关影响，也不会保存或修改配置。测试会优先使用卡片中尚未保存的输入；敏感字段留空时会回退到已经保存的 Webhook、Bot Token、SendKey 或签名密钥，Telegram Chat ID 和超时则按当前输入验证。测试消息包含“模拟成交，非实盘”，但不会创建成交记录、修改资金或持仓。

| 渠道 | 必填配置 | 可选配置 | NiuOne 接受的目标 | 配置方式 |
|---|---|---|---|---|
| 飞书 | 机器人 Webhook | 签名密钥 | `https://open.feishu.cn/open-apis/bot/v2/hook/...` 或 `https://open.larksuite.com/open-apis/bot/v2/hook/...` | [查看配置](#飞书) |
| 钉钉 | 机器人 Webhook | 签名密钥 | `https://oapi.dingtalk.com/robot/send?access_token=...` | [查看配置](#钉钉) |
| 企业微信 | 机器人 Webhook | 无 | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...` | [查看配置](#企业微信) |
| Telegram | Bot Token、Chat ID | 无 | NiuOne 根据 Token 调用官方 `api.telegram.org` Bot API | [查看配置](#telegram) |
| Server酱 | SendKey | 无 | NiuOne 根据 SendKey 前缀选择 `sctapi.ftqq.com` 或 `push.ft07.com` 推送接口 | [查看配置](#server酱) |

#### 飞书

1. 打开目标群聊，进入“设置 → 群机器人 → 添加机器人 → 自定义机器人”。不同客户端版本的入口名称可能略有差异。
2. 创建机器人后复制完整 Webhook，填入 NiuOne 的“飞书机器人 Webhook”。不要只复制路径中的 token。
3. 如在飞书机器人安全设置中启用了“签名校验”，复制页面显示的原始密钥并填入“飞书签名密钥（可选）”；NiuOne 会自动添加秒级时间戳和签名。不要填写计算后的临时签名。字段“可选”是指飞书端未启用签名时可以留空；一旦飞书端启用签名，该字段即为必填。
4. 如启用了“自定义关键词”，建议添加 `模拟成交`，确保成交消息能够通过关键词检查。关键词只在飞书机器人侧配置。
5. 如启用了 IP 白名单，需要放行运行 NiuOne 机器的公网出口 IP；本机地址 `127.0.0.1` 不是出口 IP。

飞书自定义机器人只属于创建它的当前群聊。官方当前限制为单租户单机器人 `100` 次/分钟、`5` 次/秒；Webhook 属于敏感凭据，泄露后他人可以向对应群聊发送消息。请勿将真实地址提交到 Git、问题单、日志或截图中。详细创建步骤、安全设置和错误码参见[飞书自定义机器人使用指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot?lang=zh-CN)。

#### 钉钉

1. 打开目标群聊的机器人管理入口，创建“自定义机器人”。
2. 按钉钉提示设置安全方式并复制完整 Webhook，填入 NiuOne 的“钉钉机器人 Webhook”。应使用包含 `access_token` 的 `oapi.dingtalk.com/robot/send` 地址；应用机器人或其他 OpenAPI 地址不能直接填入此处。
3. 如选择“加签”安全方式，将钉钉安全设置中显示的原始 `SEC...` 密钥填入“钉钉签名密钥（可选）”；NiuOne 会自动生成毫秒级时间戳和 URL 编码后的签名参数。不要粘贴带 `timestamp`、`sign` 的临时 URL，也不要填写计算后的签名。字段“可选”是指钉钉端未启用加签时可以留空。
4. 如同时使用关键词安全方式，建议把 `模拟成交` 配置为关键词。关键词只在钉钉机器人侧配置。
5. 如使用 IP 地址段安全方式，需要放行 NiuOne 运行机器的公网出口 IPv4 或 CIDR 网段。

签名密钥必须与当前机器人完全匹配；机器人重置安全设置后，也要在 NiuOne 中同步替换。钉钉官方当前限制每个机器人每分钟最多发送 `20` 条，超过后可能进入限流。参见[创建自定义机器人](https://open.dingtalk.com/document/dingstart/custom-bot-creation-and-installation)、[安全设置](https://open.dingtalk.com/document/dingstart/customize-robot-security-settings)、[获取 Webhook](https://open.dingtalk.com/document/dingstart/obtain-the-webhook-address-of-a-custom-robot)和[发送群消息与错误码](https://open.dingtalk.com/document/development/custom-robots-send-group-messages)。

#### 企业微信

1. 在企业微信中为目标群聊创建“消息推送（原群机器人）”。不同客户端版本的入口可能不同，请以官方“消息推送”页面为准。
2. 在“创建消息推送”“创建完成”或消息推送详情页复制该推送独有的完整 Webhook，填入 NiuOne 的“企业微信机器人 Webhook”。
3. Webhook 必须包含唯一且非空的 `key` 参数，并且消息推送必须仍属于接收通知的目标群聊。不要只填写 `key`，也不要附加其他查询参数。

企业微信渠道不需要额外签名字段，Webhook 本身即为凭据。NiuOne 会把通知正文限制在 `1900` 字节以内，低于企业微信文本消息的 `2048` 字节上限；如果短时间内频繁成交，还需留意平台的消息频率限制。若删除消息推送或重新生成 Webhook，需要在 NiuOne 中替换旧地址。接口格式参见[企业微信“消息推送配置说明”](https://developer.work.weixin.qq.com/document/path/91770)。

#### Telegram

1. 在 Telegram 中打开官方 [@BotFather](https://t.me/BotFather)，执行 `/newbot`，按提示创建机器人并保存 Bot Token。
2. 私聊接收：先打开新机器人并发送 `/start`，因为机器人不能主动与尚未开始会话的用户建立私聊。
3. 群组接收：将机器人加入目标群组，并在群中发送 `/start@机器人用户名` 等明确发给机器人的命令；默认 Privacy Mode 下，普通群消息可能不会进入机器人的更新列表。频道接收时需要把机器人设为可发消息的管理员。
4. 可先调用官方 `getMe` 方法确认 Token 有效。
5. 获取 Chat ID：向目标会话发送一条新消息后，调用官方 `getUpdates` 方法。私聊和群组通常读取 `result[].message.chat.id`，频道读取 `result[].channel_post.chat.id`，成员状态更新也可能位于 `result[].my_chat_member.chat.id`。群组和频道 ID 通常是负数，应完整复制，不要自行增删 `-100` 前缀。
6. 将 BotFather 给出的 Token 填入“Telegram Bot Token”，只填写形如 `123456:ABC...` 的 Token 本身，不要添加 `bot` 前缀或整段 API URL。将数字 Chat ID 填入“Telegram Chat ID”；公开超级群或频道也可以填写 `@channel_username`，私聊用户不能用普通 `@username` 代替数字 Chat ID。

如果 `getUpdates` 返回空数组，先确认目标会话在机器人加入后已经产生新消息；如果该机器人已配置接收更新的 Webhook，`getUpdates` 将不可用。NiuOne 只负责发送通知，不会配置 Telegram 的接收 Webhook。当前通知不设置 `message_thread_id`，因此不能定向发送到论坛群的某个指定 Topic。Bot Token 等同于机器人控制凭据，泄露后应立即通过 BotFather 撤销或重新生成。参见 Telegram 官方的[机器人创建说明](https://core.telegram.org/bots)、[`getMe` 文档](https://core.telegram.org/bots/api#getme)、[`getUpdates` 文档](https://core.telegram.org/bots/api#getupdates)和[`sendMessage` 文档](https://core.telegram.org/bots/api#sendmessage)。

#### Server酱

1. 打开 [Server酱](https://sct.ftqq.com) 并用微信扫码登录，在“SendKey”页面复制密钥。
2. 在“微信推送”等接收通道页面完成绑定，确认目标通道可以收到消息。
3. 将密钥填入 NiuOne 的“Server酱 SendKey”。Turbo 版密钥以 `SCT` 开头，Server酱³ 版密钥形如 `sctp{数字}t...`；只填写密钥本身，不要填写完整推送 URL。
4. 点击渠道卡片底部的“发送测试通知”验证配置。

NiuOne 按 SendKey 前缀自动选择推送接口：`SCT` 开头的 Turbo 密钥使用 `https://sctapi.ftqq.com/{SendKey}.send`，`sctp` 开头的 Server酱³ 密钥使用 `https://{uid}.push.ft07.com/send/{SendKey}.send`。消息标题按 Server酱 的 32 字符限制裁剪，正文使用 Markdown。SendKey 属于推送凭据，泄露后他人可以向对应通道发送消息；请勿提交到 Git、问题单、日志或截图。免费版每天 5 条、每分钟 50 条；会员上限更高。参见 Server酱官方的[接口文档](https://sct.ftqq.com/docs/integrations/coze)与[常见问题](https://sct.ftqq.com/docs/getting-started/faq)。

#### 配置项与环境变量

设置页会把配置写入私有的 `.local-data/dashboard.env`；如需手工配置，可参考 [dashboard.env.example](dashboard.env.example)。对应的 `*_NOTIFICATION_ENABLED` 开关仅表示渠道是否启用；设置页会根据已保存的渠道配置决定是否显示渠道卡片。

| 作用 | 环境变量 | 默认值 |
|---|---|---|
| 通知总开关 | `DASHBOARD_NOTIFICATION_ENABLED` | `0` |
| 单渠道请求超时 | `DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS` | `5` |
| 飞书渠道开关 | `DASHBOARD_FEISHU_NOTIFICATION_ENABLED` | `0` |
| 飞书 Webhook | `DASHBOARD_FEISHU_WEBHOOK_URL` | 空 |
| 飞书签名密钥 | `DASHBOARD_FEISHU_SIGNING_SECRET` | 空 |
| 钉钉渠道开关 | `DASHBOARD_DINGTALK_NOTIFICATION_ENABLED` | `0` |
| 钉钉 Webhook | `DASHBOARD_DINGTALK_WEBHOOK_URL` | 空 |
| 钉钉签名密钥 | `DASHBOARD_DINGTALK_SIGNING_SECRET` | 空 |
| 企业微信渠道开关 | `DASHBOARD_WECOM_NOTIFICATION_ENABLED` | `0` |
| 企业微信 Webhook | `DASHBOARD_WECOM_WEBHOOK_URL` | 空 |
| Telegram 渠道开关 | `DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED` | `0` |
| Telegram Bot Token | `DASHBOARD_TELEGRAM_BOT_TOKEN` | 空 |
| Telegram Chat ID | `DASHBOARD_TELEGRAM_CHAT_ID` | 空 |
| Server酱渠道开关 | `DASHBOARD_SERVERCHAN_NOTIFICATION_ENABLED` | `0` |
| Server酱 SendKey | `DASHBOARD_SERVERCHAN_SENDKEY` | 空 |

#### 常见问题

| 现象 | 检查项 |
|---|---|
| 所有渠道都没有消息 | 确认通知总开关已启用、至少添加并启用了一个渠道，并且确实产生了成功落盘的模拟成交。 |
| 只有某个渠道失败 | 检查对应渠道是否已启用，以及 Webhook、Token、Chat ID 是否属于同一个机器人和目标会话。 |
| 飞书 `19024` 或钉钉提示关键词不匹配 | 在机器人安全设置中加入 `模拟成交`，或调整机器人关键词规则。 |
| 飞书 `19021`、钉钉 `310000` 或提示签名/时间戳错误 | 重新复制平台显示的原始签名密钥，并校准运行 NiuOne 机器的系统时间。 |
| 飞书 `19022`、钉钉 `310000` 或提示 IP 不允许 | 将 NiuOne 机器的公网出口 IP 加入机器人白名单。 |
| 钉钉 `400101`、`400102` 或 `400106` | 检查 `access_token` 是否完整、机器人是否启用，以及机器人是否仍属于目标群。 |
| Telegram 提示 `chat not found` 或无权发送 | 先与机器人开始会话，或将机器人加入目标群组/频道并授予发消息权限，然后重新确认 Chat ID。 |
| Server酱提示 `code` 非 0 | 按返回的 `message` 排查：确认 SendKey 未失效、已关注对应的接收通道，并检查免费版每日 5 条、每分钟 50 条等配额限制。 |
| Server酱返回 429 | 该出口 IP 已超过 24 小时调用上限，停止请求后通常 24 小时自动恢复。 |
| 设置页拒绝 Webhook | 使用上述官方 HTTPS 地址，不要填写应用机器人 API、代理地址、带账号密码的 URL、非默认端口或带 `#fragment` 的地址。 |
| 设置页拒绝 Server酱 SendKey | 只填写 SendKey 本身：Turbo 版以 `SCT` 开头，Server酱³ 版形如 `sctp{数字}t...`；不要填写完整推送 URL 或他人的密钥。 |
| 移除并保存后再次添加渠道 | 所有字段应显示“未设置”，需要重新填写。若凭据可能泄露，仍应同时在对应平台撤销或轮换。 |

NiuOne 对每个启用渠道最多尝试发送一次，不自动重试，以避免响应丢失时产生重复成交提醒。推送错误只记录为告警，不会修改资金、持仓或成交日志。

默认服务只监听 `127.0.0.1`。如需通过局域网或公网访问，请先配置反向代理、HTTPS 和独立的访问控制，不要直接暴露本地管理入口。

## 运行数据与安全

本地数据默认位于 `.local-data/`：

```text
.local-data/
├── dashboard.env          # 本地运行配置，可能包含密钥
├── .venv/                 # Python 虚拟环境
├── runtime/
│   ├── config.yaml        # 服务与模型配置
│   ├── dashboard_admin_token.txt # 未配置密码时的 bootstrap 管理密钥
│   ├── *.db               # 本地数据库
│   ├── cron/              # 定时任务状态与输出
│   └── logs/              # 运行日志
└── backups/               # 本地部署备份
```

`.local-data/` 已被 Git 忽略。提交代码、公开日志或分享截图前，请确认其中不包含 API Key、管理员凭据、数据库内容或其他个人数据。

## 长期运行与更新

使用同一个一键启动脚本增加 `--service` 参数，即可完成依赖初始化、原生后台服务注册和启动。

macOS / Linux：

```bash
./run.sh --service
```

Windows：

```cmd
run.bat --service
```

macOS 使用 LaunchAgent，Linux 使用用户级 systemd，Windows 使用任务计划程序。该模式会托管 dashboard、定时调度器和关注源监控三个进程；未启用的关注源功能会保持休眠。

需要指定端口或禁止自动打开浏览器时，可组合参数：

```bash
./run.sh --service --port 8877 --no-browser
```

源码部署升级前先备份 `.local-data/`，然后在没有未提交代码冲突的前提下同步默认分支并重新运行启动器：

```bash
git pull --ff-only
./run.sh --service --no-browser
```

尚未安装长期运行服务的前台部署，可把第二条命令改为 `./run.sh --no-browser`。macOS、Linux 或 Windows 已安装长期运行服务时，普通启动命令会自动识别并重启托管进程，避免只更新前端而旧后端继续运行。启动器会保留 `.local-data/`：虚拟环境缺失或 `requirements.txt` 变化时安装 Python 依赖，前端源码、样式或锁文件变化时重新构建 Vue。设置页的版本检查只负责提示，不会替用户执行升级。容器部署应修改 `NIUONE_IMAGE` 为明确的新版本标签后再执行 `docker compose pull` 和 `docker compose up -d --no-build`。

各平台的状态、重启、卸载和无人值守运行说明参见 [独立运行说明](docs/STANDALONE.md)。部署更新、日志检查、备份和回滚步骤参见 [部署、验证和回滚手册](docs/OPERATIONS.md)。

## 项目结构

```text
.
├── app/                    # 按领域组织的应用源码
│   ├── entrypoints/        # Dashboard、调度器、监控与报告启动入口
│   ├── compat/             # 历史裸模块名适配器
│   ├── core/               # 路径、缓存等跨领域基础设施
│   ├── automation/         # Cron 规则与定时任务调度
│   ├── backtesting/        # 独立的选股信号与策略买卖回测
│   ├── dashboard/          # Dashboard 服务与 API
│   ├── market_data/        # 行情访问与证券代码工具
│   ├── messaging/          # 通知渠道、分发与成交消息
│   ├── reports/            # A 股、美股报告
│   ├── monitoring/         # X 等监控工作流
│   ├── screening/          # 多策略选股与候选增强
│   ├── storage/            # 消息历史与模拟盘存储
│   ├── trading/            # 模拟交易与优化器
│   └── strategies/         # 策略注册、评分、筛选、归因、退出与 Prompt
├── config/                 # 运行策略与安全约定
├── docs/                   # 部署、运行和研究文档
├── scripts/                # 验证、部署和独立任务脚本
├── tests/                  # 自动化测试
├── tools/                  # 本地维护工具
├── web/                    # Vue 3、Vite 配置、组件与依赖锁
├── frontend/               # Vue 构建复用的 Dashboard 与管理页样式
├── dashboard.env.example   # 配置示例
├── run.sh                  # macOS / Linux 一键启动
├── run.bat                 # Windows 一键启动
└── requirements.txt        # Python 依赖
```

## 验证

服务启动后可执行健康检查：

```bash
curl -s -o /dev/null -w 'HEALTH HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/healthz
curl -s -o /dev/null -w 'READY HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/readyz
curl -s -o /dev/null -w 'SNAPSHOT HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/api/v2/public/latest
```

`healthz` 和公开快照应返回 `HTTP:200`。首次部署会立即初始化牛牛策略所需的全市场日 K；这段时间 `readyz` 返回 `HTTP:503` 是正常状态，就绪后会变为 `HTTP:200`。实战页会显示完成数、覆盖率和部署提醒。

开发验证：

```bash
./scripts/validate.sh
```

验证脚本会构建 Vue 生产版本，检查 Python、JavaScript、Shell、Windows BAT 入口，并运行 `tests/` 下的自动化测试。

## 常见问题

### 找不到 `python3`

安装 Python 3.11 或更高版本，并确认 `python3 --version` 可以正常输出版本号。Windows 启动脚本会依次尝试 `python`、`py -3` 和 `python3`。

### 依赖安装失败

首次启动需要从 PyPI 下载依赖。请检查网络和本机 pip 配置，然后重新运行启动脚本。中国大陆网络出现连接超时时，可按[独立运行说明](docs/STANDALONE.md#中国大陆首次安装超时)配置用户级 PyPI 镜像以及有限的超时和重试次数。

### 端口 `8787` 已被占用

指定其他端口：

```bash
./run.sh --port 8877
```

### 页面可访问，但部分内容没有生成

检查设置页中的数据源、模型服务、功能开关和任务时间，并确认相关外部服务可访问。更多排查方法见 [部署、验证和回滚手册](docs/OPERATIONS.md)。

### 手动选股总是在 480 秒后超时

精确的 480 秒表示完整扫描触发了服务端硬超时，通常不是浏览器问题。先打开实战页的数据准备卡片，或请求 `/api/system/data-readiness`：首次部署应等待日 K 覆盖率达到安全阈值；若初始化失败，检查腾讯行情连通性、运行目录写权限以及 Docker `/data` 持久化卷。新版会在启动后立即补齐缓存，手动任务展示当前阶段，行情、日 K、评分等阶段超时会返回不同错误码；不建议仅把 480 秒调大来掩盖冷启动或上游故障。

## 文档

- [独立运行说明](docs/STANDALONE.md)：一键启动、模型配置、长期服务与升级。
- [部署、验证和回滚手册](docs/OPERATIONS.md)：配置口径、任务归属、排障、部署与恢复。
- [Dashboard 增量展示与部署](docs/DASHBOARD_V2.md)：Vue/FastAPI、公开快照、缓存与反向代理。
- [策略研究说明](docs/strategies/README.md)：内置策略、牛牛战法、风险预算与扩展方式。
- [app 模块结构](docs/APP_ARCHITECTURE.md)：入口、兼容层、领域边界与依赖方向。
- [容器镜像发布流程](docs/CONTAINER_RELEASE.md)：维护者发布多架构 Docker 镜像的步骤。
- [运行数据和敏感信息处理策略](config/runtime-policy.md)：私有目录、密钥、数据库与泄露处理。

## License

NiuOne 使用 [Apache License 2.0](LICENSE) 发布。
