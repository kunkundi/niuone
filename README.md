<img width="2115" height="744" alt="niuone" src="https://github.com/user-attachments/assets/50dd932a-5af9-441a-b17a-d63a0b6801ac" />

# NiuOne · 牛牛1号

简体中文 | [English](README_EN.md)

<p align="left">
  <a href="https://linux.do"><img src="https://shorturl.at/ggSqS" alt="LINUX DO" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License" /></a>
  <a href="https://github.com/kunkundi/niuone/actions/workflows/ci.yml"><img src="https://github.com/kunkundi/niuone/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://hub.docker.com/r/kunkundi/niuone"><img src="https://img.shields.io/docker/pulls/kunkundi/niuone?label=Docker%20Pulls" alt="Docker Pulls" /></a>
</p>

## 项目简介

NiuOne（牛牛1号）是一套面向 A 股市场的研究与模拟交易系统，由大模型驱动行情分析和交易决策，并提供行情聚合、消息面检索、策略配置及模拟账户跟踪等能力。

系统通过 Web 看板统一展示市场行情、分析结果和模拟账户状态。定时任务自动采集盘前竞价、盘中及盘后行情、资金流向、板块表现、隔夜美股、机构评级和推特关注源等信息。大模型在用户配置的策略规则约束下，完成消息面检索、行情研判与模拟买卖决策。账户状态、交易记录和决策依据保存在本地，模拟成交可通过飞书、钉钉、企业微信或 Telegram 推送提醒。

NiuOne 将信息采集、市场分析、交易决策和账户记录串联为可自动运行、可追溯、可复盘的模拟交易闭环。系统可部署于个人计算机或服务器，配置和研究数据由用户自行管理。所有交易均在模拟账户中执行，不连接券商交易接口，不涉及真实资金。

## 在线演示

<https://niuone.cn>

> 本页面仅用于个人研究、模拟交易和信息展示，不构成证券、期货投资咨询、投资建议、荐股服务或任何买卖依据；不承诺收益，不代客理财，不收取荐股费用。

## 功能概览

- **统一看板**：集中展示指数、板块、市场热度、资金流和历史消息。
- **信息聚合**：整理 A 股盘面、美股市场摘要、机构评级和推特关注源。
- **智能摘要**：可接入兼容的大模型服务，对多来源信息进行归纳和结构化整理。
- **自定义交易策略**：可以选择内置策略，也可以使用自然语言编写自己的候选、买入、卖出、仓位和时间规则。
- **模拟交易与账户跟踪**：通过用户自己的模拟账户完成候选筛选、买卖决策、持仓与盈亏跟踪，并查看收益曲线和交易日志，全程不连接券商、不使用真实资金。
- **自动化任务**：支持定时采集、生成摘要、数据库入库和后台监控。
- **数据自主管理**：配置、数据库、日志和任务输出默认保存在独立的运行目录中，由用户自行保存和管理，不随源码提交。

具体研究方法与实验性策略不在主 README 展开，参见 [策略研究说明](docs/strategies/README.md)。

参与开发或扩展功能时，模块边界与兼容入口约定参见 [app 模块结构](docs/APP_ARCHITECTURE.md)。

Dashboard 已迁移到 Vue 3 + Vite 与 FastAPI/Uvicorn，保持原有页面布局，并通过同源增量快照减少公网传输；交易、行情请求和记录计算全部在服务端完成。公开页、`/admin` 和所有 API 共用一个生产端口。架构、缓存和 CDN/云主机/Tunnel 部署方式见 [Dashboard 增量展示与部署](docs/DASHBOARD_V2.md)。

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
| `--skip-install` | 跳过依赖安装检查 |
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

项目提供单一镜像和 Compose 编排。Compose 会启动 dashboard、定时调度器和推特关注源守护进程，并通过同一个 `niuone-data` volume 持久化配置、数据库、日志和任务输出。

从源码构建并启动：

```bash
docker compose up -d --build
docker compose ps
```

默认在宿主机 `127.0.0.1:8787` 提供服务；公开页面和受密码保护的 `/admin` 管理页使用同一端口。查看日志或停止服务：

```bash
docker compose logs -f
docker compose down
```

从 Docker Hub 部署指定版本：

```bash
export NIUONE_IMAGE=kunkundi/niuone:v0.0.1
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

启动后通过页面中的设置入口完成配置；先使用配置的管理员密码或本地 bootstrap 管理密钥完成认证。配置会写入本地 `.local-data/`，无需修改源码。建议首次使用时依次完成：

1. 设置需要启用的数据源与自动化任务；
2. 按需配置兼容的模型服务地址、模型名称和 API Key；
3. 如需成交提醒，在“交易通知”中开启总开关，再从下拉框添加所需渠道并填写对应配置；Telegram 需填写 Bot Token 与 Chat ID；
4. 妥善保存或轮换管理员凭据；
5. 重启服务，使所有需要重启的配置生效。

### 交易通知配置

NiuOne 支持将模拟买入和卖出成交推送到飞书、钉钉、企业微信和 Telegram。通知只在成交状态成功落盘后发送；同一轮的多笔成交会合并为一条消息，并明确标注“模拟成交，非实盘”。单个渠道发送失败不会回滚成交，也不会影响其他渠道。

#### 在设置页添加渠道

1. 进入“设置 → 交易通知”，将“启用模拟成交通知”设为“启用”。
2. “单次推送超时秒数”默认是 `5` 秒，可设置为 `1`–`30` 秒。
3. 在“通知渠道”下拉框中选择渠道，点击“添加渠道”。
4. 填写该渠道卡片中的必填字段，按需填写签名密钥；使用卡片右上角的状态开关决定该渠道是否接收成交通知，开关旁会明确显示“已启用”或“已关闭”。
5. 点击卡片底部的“发送测试通知”验证配置。测试成功后保存业务配置，再按需添加其他渠道；保存后通知配置会热生效，无需为通知配置单独重启服务。

“关闭”只停止该渠道的成交通知，不会删除 Webhook、Bot Token、Chat ID 或签名密钥，之后可直接重新启用。点击渠道卡片右上角的“移除”会关闭并收起该渠道；点击“保存本组设置”后，NiuOne 才会删除该渠道已经保存的全部配置，再次添加时状态为“未设置”。如果在保存前重新添加渠道，原配置不会被删除。对于仍处于添加状态的渠道，敏感字段留空保存表示保留旧值。

“发送测试通知”只向当前卡片对应的一个渠道发送，不受通知总开关或渠道开关影响，也不会保存或修改配置。测试会优先使用卡片中尚未保存的输入；敏感字段留空时会回退到已经保存的 Webhook、Bot Token 或签名密钥，Telegram Chat ID 和超时则按当前输入验证。测试消息包含“模拟成交，非实盘”，但不会创建成交记录、修改资金或持仓。

| 渠道 | 必填配置 | 可选配置 | NiuOne 接受的目标 | 配置方式 |
|---|---|---|---|---|
| 飞书 | 机器人 Webhook | 签名密钥 | `https://open.feishu.cn/open-apis/bot/v2/hook/...` 或 `https://open.larksuite.com/open-apis/bot/v2/hook/...` | [查看配置](#飞书) |
| 钉钉 | 机器人 Webhook | 签名密钥 | `https://oapi.dingtalk.com/robot/send?access_token=...` | [查看配置](#钉钉) |
| 企业微信 | 机器人 Webhook | 无 | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...` | [查看配置](#企业微信) |
| Telegram | Bot Token、Chat ID | 无 | NiuOne 根据 Token 调用官方 `api.telegram.org` Bot API | [查看配置](#telegram) |

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
| 设置页拒绝 Webhook | 使用上述官方 HTTPS 地址，不要填写应用机器人 API、代理地址、带账号密码的 URL、非默认端口或带 `#fragment` 的地址。 |
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

各平台的状态、重启、卸载和无人值守运行说明参见 [独立运行说明](docs/STANDALONE.md)。部署更新、日志检查、备份和回滚步骤参见 [部署、验证和回滚手册](docs/OPERATIONS.md)。

## 项目结构

```text
.
├── app/                    # 按领域组织的应用源码
│   ├── entrypoints/        # Dashboard、调度器、监控与报告启动入口
│   ├── compat/             # 历史裸模块名适配器
│   ├── core/               # 路径、缓存等跨领域基础设施
│   ├── automation/         # Cron 规则与定时任务调度
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
├── frontend/               # 迁移期 CSS 和旧控制器兼容资源
├── dashboard.env.example   # 配置示例
├── run.sh                  # macOS / Linux 一键启动
├── run.bat                 # Windows 一键启动
└── requirements.txt        # Python 依赖
```

## 验证

服务启动后可执行健康检查：

```bash
curl -s -o /dev/null -w 'HEALTH HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/healthz
curl -s -o /dev/null -w 'SNAPSHOT HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/api/v2/public/latest
```

预期均返回 `HTTP:200`。

开发验证：

```bash
./scripts/validate.sh
```

验证脚本会构建 Vue 生产版本，检查 Python、JavaScript、Shell、Windows BAT 入口，并运行 `tests/` 下的自动化测试。

## 常见问题

### 找不到 `python3`

安装 Python 3.11 或更高版本，并确认 `python3 --version` 可以正常输出版本号。Windows 启动脚本会依次尝试 `python`、`py -3` 和 `python3`。

### 依赖安装失败

首次启动需要从 PyPI 下载依赖。请检查网络和本机 pip 配置，然后重新运行启动脚本。

### 端口 `8787` 已被占用

指定其他端口：

```bash
./run.sh --port 8877
```

### 页面可访问，但部分内容没有生成

检查设置页中的数据源、模型服务、功能开关和任务时间，并确认相关外部服务可访问。更多排查方法见 [部署、验证和回滚手册](docs/OPERATIONS.md)。

## 文档

- [策略研究说明](docs/strategies/README.md)
- [独立运行说明](docs/STANDALONE.md)
- [部署、验证和回滚手册](docs/OPERATIONS.md)
- [运行数据和敏感信息处理策略](config/runtime-policy.md)

## License

NiuOne 使用 [Apache License 2.0](LICENSE) 发布。
