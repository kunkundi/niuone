# 部署、验证和回滚手册

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
├── run.ps1                 # Windows PowerShell 启动
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

如需在启动时设置并保存管理员密码：

```bash
./run.sh --admin-password "change-this-to-a-strong-password"
```

Windows PowerShell 使用 `.\run.ps1 --admin-password "change-this-to-a-strong-password"`。

首次运行会创建 `.local-data/.venv`、安装依赖、生成 `.local-data/dashboard.env`，然后启动：

```text
http://127.0.0.1:8787/
```

部署到非本机环境前，至少确认：

- `DASHBOARD_AUTH_ENABLED=1`
- `DASHBOARD_HOST` 符合你的部署环境和网络边界
- `DASHBOARD_EDGE_CACHE_ENABLED=0`
- 设置页管理员密码或本地备用管理员凭据已妥善保存

## 3. 模型配置

NiuOne 需要大模型驱动完整工作流。X 关注列表监控和美股机构评级日报推荐使用 Grok；A 股盘面总结增强可使用任意兼容 `/chat/completions` 的模型；A 股候选股消息面预检可独立配置具备实时搜索能力的模型；选股后的买卖决策可配置兼容模型，推荐使用 DeepSeek。

核心配置项：

| 场景 | 配置项 |
|---|---|
| 牛牛美股总开关 | `DASHBOARD_US_FEATURES_ENABLED` |
| Grok API | `DASHBOARD_GROK_BASE_URL`、`DASHBOARD_GROK_API_KEY`、`DASHBOARD_GROK_MODEL` |
| A 股盘面模型总结单独覆盖 | `A_SHARE_MODEL_SUMMARY_BASE_URL`、`A_SHARE_MODEL_SUMMARY_API_KEY`、`A_SHARE_MODEL_SUMMARY_MODEL` |
| 消息面预检 API | `DASHBOARD_NEWS_BASE_URL`、`DASHBOARD_NEWS_API_KEY`、`DASHBOARD_NEWS_MODEL` |
| 买卖决策 API | `DASHBOARD_DECISION_BASE_URL`、`DASHBOARD_DECISION_API_KEY`、`DASHBOARD_DECISION_MODEL` |
| 美股评级单独覆盖 | `US_RATING_BASE_URL`、`US_RATING_API_KEY`、`US_RATING_MODEL` |
| X 关注列表单独覆盖 | `X_WATCHLIST_BASE_URL`、`X_WATCHLIST_API_KEY`、`X_WATCHLIST_MODEL` |

优先通过页面上的设置按钮进入设置页维护。推文监控和美股评级相关设置由“开启牛牛美股”开关控制；关闭时设置页会隐藏这些项，后台 X 监控和美股评级定时任务也会跳过。也可以直接编辑 `.local-data/dashboard.env`，保存后按配置影响范围重启或等待下一轮任务读取。

## 4. 验证流程

```bash
./scripts/validate.sh
```

验证内容：

1. Python 语法检查
2. 内嵌前端 JavaScript 语法检查
3. Shell 启动脚本语法检查
4. PowerShell 脚本语法检查（环境存在 PowerShell 时）
5. `tests/` 单元测试

隔离实例验证：

```bash
DASHBOARD_HOME=/tmp/niuone-smoke DASHBOARD_AUTH_ENABLED=0 DASHBOARD_PORT=8878 ./scripts/run_standalone.sh
```

健康检查：

```bash
curl -s -o /dev/null -w 'HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8878/
curl -s -o /dev/null -w 'HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8878/api/auth/status
```

预期均返回 `HTTP:200`。

## 5. 本机长期运行

macOS LaunchAgent 文件通常位于：

```text
~/Library/LaunchAgents/ai.niuone.dashboard.plist
~/Library/LaunchAgents/ai.niuone.cron-scheduler.plist
~/Library/LaunchAgents/ai.niuone.x-watchlist.plist
```

它们应分别调用：

```text
/path/to/NiuOne/run-dashboard.sh
/path/to/NiuOne/run-niuone-cron-scheduler.sh
/path/to/NiuOne/run-x-watchlist-daemon.sh
```

查看状态：

```bash
launchctl print gui/$(id -u)/ai.niuone.dashboard | sed -n '1,100p'
launchctl print gui/$(id -u)/ai.niuone.cron-scheduler | sed -n '1,100p'
launchctl print gui/$(id -u)/ai.niuone.x-watchlist | sed -n '1,100p'
```

重启：

```bash
launchctl kickstart -k gui/$(id -u)/ai.niuone.dashboard
launchctl kickstart -k gui/$(id -u)/ai.niuone.cron-scheduler
launchctl kickstart -k gui/$(id -u)/ai.niuone.x-watchlist
```

## 6. 部署流程

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
- 访问 `/login` 做 smoke check

如果服务由 LaunchAgent 托管，`HUP` 后通常会由托管器拉起新进程；如果没有托管器，请手动重新运行 `./run.sh` 或对应启动脚本。

部署后检查：

```bash
curl -s -o /dev/null -w 'LOGIN HTTP:%{http_code} TOTAL:%{time_total}\n' http://127.0.0.1:8787/login
TOKEN=$(cat .local-data/runtime/dashboard_admin_token.txt)
curl -s "http://127.0.0.1:8787/api/messages?limit=1&token=$TOKEN" | python3 -m json.tool | head
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
curl -s -o /dev/null -w 'HTTP:%{http_code}\n' http://127.0.0.1:8787/login
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
TOKEN=$(cat .local-data/runtime/dashboard_admin_token.txt)
curl -s "http://127.0.0.1:8787/api/messages?limit=5&token=$TOKEN" | python3 -m json.tool | head
```

当前消息流以 `push_history.db` 为主要来源。任务脚本需要正常写入该数据库后，页面才会出现对应消息。

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

该脚本会抽取 `app/niuone_dashboard.py` 内嵌 JavaScript 并执行语法检查。

### 不要提交真实数据

提交前检查：

```bash
git status --ignored --short
```

`.local-data/` 应显示为 ignored，不应出现在 staged files 中。

## 10. 维护原则

1. 改动源码后运行 `./scripts/validate.sh`。
2. 临时测试使用独立 `DASHBOARD_HOME=/tmp/...` 和非 8787 端口。
3. 多人或远程访问必须开启认证和限流。
4. 真实数据库、本地凭据、日志、模型配置只留在 `.local-data/`。
5. 新任务应写入 `push_history.db` 或当前归档目录，避免只生成孤立文件。
