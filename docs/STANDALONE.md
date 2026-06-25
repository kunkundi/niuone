# Standalone Dashboard Runtime

Dashboard 是 NiuOne 自己的独立运行时。核心约定：

- `DASHBOARD_HOME`：dashboard 自己的运行数据目录，默认工程目录内 `.local-data/runtime`
- 源码 helper 默认从当前 `app/` 目录加载

## 一键本地启动

```bash
cd /path/to/NiuOne
./run.sh
```

首次运行会自动创建 `.local-data/.venv`、安装依赖、生成 `.local-data/dashboard.env`，
并启动 Dashboard。

系统入口：

| 系统 | 一键启动方式 |
|---|---|
| macOS | 双击 `run.command`，或终端执行 `./run.sh` |
| Windows | 双击 `run.bat`，或 PowerShell 执行 `.\run.ps1` |
| Linux | 终端执行 `./run.sh`，桌面环境可尝试双击 `run.desktop` |

访问：

```text
http://127.0.0.1:8787/
```

本地一键入口默认 `DASHBOARD_AUTH_ENABLED=0`，只适合监听 `127.0.0.1` 的本机体验。
如果要暴露到局域网或公网，请编辑 `.local-data/dashboard.env` 开启认证。

## 最小独立启动

```bash
cd /path/to/NiuOne
DASHBOARD_PORT=8877 ./scripts/run_standalone.sh
```

访问：

```text
http://127.0.0.1:8877/
```

## 独立运行时文件

默认会写入：

```text
.local-data/runtime/
├── dashboard_users.db
├── dashboard_admin_token.txt
├── push_history.db
├── state.db                 # 如果存在 Hermes legacy state 导入需求
├── logs/                    # legacy gateway 日志目录，可空
└── cron/output/             # B1/cache/helper 输出
```

## 关键环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `DASHBOARD_HOME` | `.local-data/runtime` | 独立运行数据根目录 |
| `DASHBOARD_PORT` | `8877` in `run_standalone.sh` | 监听端口 |
| `DASHBOARD_HOST` | `127.0.0.1` | 监听地址 |
| `DASHBOARD_AUTH_ENABLED` | `1` | 是否启用邀请码登录 |
| `DASHBOARD_TRADER_SCRIPT` | `app/niuniu_practice_trader.py` | 牛牛实战模块路径 |
| `DASHBOARD_B1_SCANNER` | `app/multi_strategy_screen.py` | 可选 B1 扫描脚本路径 |
| `DASHBOARD_PUSH_HISTORY_DB` | `$DASHBOARD_HOME/push_history.db` | 消息 DB 路径 |
| `DASHBOARD_PORTFOLIO_STATE` | `$DASHBOARD_HOME/cron/output/niuniu_practice_portfolio.json` | 模拟账户状态 |
| `DASHBOARD_CONFIG` | `$DASHBOARD_HOME/config.yaml` | 模型/provider 配置 |
| `DASHBOARD_US_RATING_OUTPUT_DIR` | `$DASHBOARD_HOME/cron/output/fd0b807138f4` | 美股买入评级日报归档目录 |

管理员可打开 `/admin` 的“运行配置 / 模型配置”区块，维护 `dashboard.env`
和 `DASHBOARD_CONFIG` 指向的 YAML。启动时读取的环境变量保存后需要重启
服务生效；`config.yaml` 会由相关任务在下次读取时使用。

## 独立任务脚本

```bash
cd /path/to/NiuOne

# 生成美股机构买入评级日报，并写入 dashboard 归档/消息库
./scripts/run_us_rating_report.sh
```

## 与旧 Hermes 部署兼容

线上旧部署可以继续设置：

```bash
python3 /path/to/NiuOne/app/niuone_dashboard.py --host 127.0.0.1 --port 8787
```

如果未来要完全迁移，可以：

```bash
mkdir -p ~/.niuniu-dashboard
cp ~/.hermes/dashboard_users.db ~/.niuniu-dashboard/  # 可选
cp ~/.hermes/dashboard_admin_token.txt ~/.niuniu-dashboard/  # 可选
cp ~/.hermes/push_history.db ~/.niuniu-dashboard/  # 可选
cp -R ~/.hermes/cron ~/.niuniu-dashboard/  # 可选
```

然后用 `DASHBOARD_HOME=~/.niuniu-dashboard` 启动即可。
