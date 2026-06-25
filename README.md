# NiuOne · 牛牛1号

[![CI](https://github.com/kunkundi/niuone/actions/workflows/ci.yml/badge.svg)](https://github.com/kunkundi/niuone/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

NiuOne 是一个本地优先的市场信息与交易辅助工具。它把 A 股市场面板、策略筛选、模拟交易、X 关注列表监控、美股机构评级摘要和定时任务归档集中在同一个轻量 Python 服务中。

项目默认将所有运行数据、数据库、日志、token 和本地虚拟环境写入 `.local-data/`。该目录已被 `.gitignore` 忽略，适合把源码公开到 GitHub，同时把真实数据保留在工程目录内。

> NiuOne 仅用于研究、信息整理和个人决策辅助，不构成任何投资建议。

## 功能概览

- **一键本地运行**：macOS、Windows、Linux 均提供命令行启动入口，首次运行自动创建虚拟环境并安装依赖。
- **聚合视图**：展示消息历史、指数、板块、热门股票、资金流、市场流向和策略结果。
- **策略与模拟交易**：集成 B1 策略扫描、牛牛实战模拟账户、持仓和收益曲线展示。
- **定时任务归档**：支持市场监控、美股机构评级日报、X 关注列表监控等任务输出。
- **本地访问控制**：支持邀请码、管理员 token、管理员密码、限流和运行配置管理。

## 系统要求

通用要求：

| 依赖 | 用途 |
|---|---|
| Python 3.11+ | 运行本地服务、创建虚拟环境、执行任务脚本 |
| Git | 克隆项目 |

平台相关：

| 平台 | 额外说明 |
|---|---|
| macOS / Linux | 不需要 PowerShell；使用 `./run.sh` |
| Windows | 需要 PowerShell 执行 `run.ps1`；系统通常自带 Windows PowerShell |

Python 依赖由一键启动脚本自动安装，当前核心依赖见 [requirements.txt](requirements.txt)。

## 快速开始

```bash
git clone https://github.com/kunkundi/niuone.git
cd niuone
```

| 系统 | 启动方式 |
|---|---|
| macOS | 终端执行 `./run.sh` |
| Windows | PowerShell 执行 `.\run.ps1` |
| Linux | 终端执行 `./run.sh` |

本地一键启动默认关闭访问认证，且管理员密码为空；此时本机访问 `/admin` 不需要额外密码。长期运行、多人使用或暴露到非本机网络前，建议启动时设置管理员密码：

```bash
./run.sh --admin-password "change-this-to-a-strong-password"
```

Windows PowerShell：

```powershell
.\run.ps1 --admin-password "change-this-to-a-strong-password"
```

启动后浏览器会自动打开：

```text
http://127.0.0.1:8787/
```

首次运行会自动完成：

- 创建 `.local-data/`
- 创建 `.local-data/.venv`
- 安装 `requirements.txt`
- 生成本地配置文件
- 将数据库、token、日志、任务输出写入 `.local-data/runtime/`

Linux 如果提示没有执行权限：

```bash
chmod +x run.sh
```

## 配置

首次启动会在 `.local-data/` 中生成本地配置文件。

长期运行或部署到非本机环境前，请至少检查：

| 配置项 | 说明 |
|---|---|
| 监听地址 | 默认 `127.0.0.1` |
| 监听端口 | 默认 `8787` |
| 访问认证 | 本地一键启动默认关闭；多人或远程访问必须开启 |
| 管理页密码 | 保护 `/admin` 设置页和管理接口，可留空仅使用 admin token |
| 模型配置 | 用于配置模型 provider 和密钥 |

NiuOne 需要接入大模型后才能驱动完整工作流。X 关注列表监控和美股机构评级日报推荐使用 Grok，并由“开启牛牛美股”开关控制；A 股候选股消息面预检可独立配置具备实时搜索能力的模型；选股后的买卖决策可配置兼容模型，推荐使用 DeepSeek。启动后，管理员可通过 `/admin` 管理运行配置、模型配置和邀请码。

### 管理员密码

管理员密码用于保护 `/admin` 设置页及其管理接口，包括运行配置、模型配置、邀请码和访问用户管理。它不是普通访问邀请码，而是管理员进入设置页时的额外保护层。

首次启动会生成 `.local-data/dashboard.env`，其中包含：

```env
DASHBOARD_AUTH_ENABLED=0
DASHBOARD_ADMIN_PASSWORD=
```

本地一键启动默认关闭访问认证，且管理员密码为空；此时本机访问 `/admin` 不需要额外密码。长期运行、多人使用或暴露到非本机网络前，建议至少改为：

```env
DASHBOARD_AUTH_ENABLED=1
DASHBOARD_ADMIN_PASSWORD=change-this-to-a-strong-password
```

也可以在一键启动时传入管理员密码，启动脚本会把它保存到 `.local-data/dashboard.env`：

```bash
./run.sh --admin-password "change-this-to-a-strong-password"
```

Windows PowerShell：

```powershell
.\run.ps1 --admin-password "change-this-to-a-strong-password"
```

修改 `.local-data/dashboard.env` 后需要重启服务。也可以在首次启动前通过 `DASHBOARD_ADMIN_PASSWORD` 环境变量提供默认值；配置文件已存在时，建议使用启动参数、设置页或直接编辑配置文件更新。

管理员 token 会在服务启动时自动生成并保存到 `.local-data/runtime/dashboard_admin_token.txt`。开启访问认证后，可以用该 token 进入管理员身份，例如：

```text
http://127.0.0.1:8787/admin?token=<token-from-file>
```

如果 `DASHBOARD_ADMIN_PASSWORD` 为空，admin token 即可访问设置页；如果设置了管理员密码，先通过 admin token 获得管理员身份，再输入管理员密码解锁设置页。请不要把 `.local-data/dashboard.env` 或 `.local-data/runtime/dashboard_admin_token.txt` 提交到 Git。

命令行参数可能留在 shell 历史或短暂出现在进程列表中；对更敏感的部署，建议直接编辑 `.local-data/dashboard.env`，或先启动后在 `/admin` 设置页中修改。

## 项目结构

```text
.
├── app/                    # 本地服务和任务源码
├── tests/                  # 单元测试
├── scripts/                # 验证、迁移和独立任务脚本
├── docs/                   # 操作文档
├── config/                 # 运行策略说明
├── run.sh                  # macOS/Linux 一键启动
├── run.ps1                 # Windows PowerShell 启动
└── requirements.txt        # Python 依赖清单
```

## 文档

- [docs/STANDALONE.md](docs/STANDALONE.md)：独立运行说明
- [docs/OPERATIONS.md](docs/OPERATIONS.md)：部署、验证和回滚手册
- [config/runtime-policy.md](config/runtime-policy.md)：运行数据和 secrets 处理策略

## License

NiuOne 使用 [Apache License 2.0](LICENSE) 发布。
