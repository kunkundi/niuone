# 运行数据和敏感信息处理策略

简体中文 | [English](runtime-policy_EN.md)

本文档定义 NiuOne 的运行数据、模型密钥和本地私有文件处理规则。目标是让真实数据可以留在工程目录内，同时确保上传到公开仓库的内容不包含用户数据或敏感信息。

## 目录边界

源码目录：

```text
/path/to/NiuOne
```

私有运行目录：

```text
.local-data/
├── dashboard.env
├── .venv/
├── runtime/
└── backups/
```

`.local-data/`、`dashboard.env`、数据库、本地凭据、日志和备份文件都已在 `.gitignore` 中忽略。

## 不应提交或外传的内容

| 路径 | 说明 |
|---|---|
| `.local-data/dashboard.env` | 本机环境变量、路径和可能存在的模型密钥或管理员密码 |
| `.local-data/.venv/` | 本机 Python 虚拟环境 |
| `.local-data/runtime/dashboard_admin_token.txt` | 未配置 `DASHBOARD_ADMIN_PASSWORD` 时使用的 bootstrap 管理密钥 |
| `.local-data/runtime/dashboard_users.db` | 本地访问用户和认证数据 |
| `.local-data/runtime/push_history.db` | 消息历史 |
| `.local-data/runtime/niuniu.db` | 实战页面交易和账户数据 |
| `.local-data/runtime/config.yaml` | 模型服务商、模型和模型密钥配置 |
| `.local-data/runtime/cron/state/` | 定时任务、X 监控和补跑状态 |
| `.local-data/runtime/cron/output/` | 实战选股缓存、模拟账户状态和其他非消息类运行缓存 |
| `.local-data/runtime/logs/` | 服务和任务日志 |
| `.local-data/backups/` | 部署备份，可能包含旧配置 |

不要把上述内容复制到 issue、PR、README、文档示例或聊天上下文。排查问题时只提供脱敏后的错误类型、时间点和必要字段。

## 模型密钥

推荐用途：

| 用途 | 推荐模型 | 配置项 |
|---|---|---|
| X 关注列表监控、美股机构评级日报 | Grok | `DASHBOARD_GROK_BASE_URL`、`DASHBOARD_GROK_API_KEY`、`DASHBOARD_GROK_MODEL`、`DASHBOARD_GROK_API_MODE` |
| A 股盘面总结增强 | 兼容 `/chat/completions` 的模型 | `A_SHARE_MODEL_SUMMARY_BASE_URL`、`A_SHARE_MODEL_SUMMARY_API_KEY`、`A_SHARE_MODEL_SUMMARY_MODEL`；留空时复用 `DASHBOARD_GROK_*` |
| A 股候选股消息面预检 | 具备实时搜索能力的模型 | `DASHBOARD_NEWS_BASE_URL`、`DASHBOARD_NEWS_API_KEY`、`DASHBOARD_NEWS_MODEL`、`DASHBOARD_NEWS_API_MODE` |
| 选股后的买卖决策 | 推荐 DeepSeek，可用其他兼容模型 | `DASHBOARD_DECISION_BASE_URL`、`DASHBOARD_DECISION_API_KEY`、`DASHBOARD_DECISION_MODEL` |
| 综合决策参考 | 本地聚合，不需要额外模型 | `DASHBOARD_DECISION_INTELLIGENCE_ENABLED`、`DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS`、`DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` |

X 关注列表监控和美股机构评级日报由 `DASHBOARD_US_FEATURES_ENABLED` 总开关控制。关闭时设置页隐藏相关配置，后台 X 守护进程和美股评级定时任务跳过执行。

综合决策参考会读取本地行情缓存、盘面消息历史和模拟账户状态，并把压缩后的摘要写入决策日志；它不新增模型密钥，但日志中可能包含候选消息面摘要，公开排障前仍需按运行数据策略检查。

模型密钥只允许保存在 `.local-data/dashboard.env`、`.local-data/runtime/config.yaml` 或受控的系统环境变量中。提交前必须确认没有新增 `.env`、`*.key`、`*.token`、`*.secret`、数据库或备份文件。

问财数据源使用 `IWENCAI_API_KEY`，同样只允许保存到 `.local-data/dashboard.env` 或受控系统环境变量。
`IWENCAI_ENABLED` 默认关闭；问财数据仅作为研究快照和现有行情的补充，不得用不完整或缓存响应覆盖账户、成交和真实交易记录。
龙虎榜任务默认在 A 股交易日北京时间 18:00 更新；只有非空成功响应可以原子替换最新快照并写入交易日归档，失败或空结果必须保留上一份有效数据。买卖前五席位明细单独失败时保留同一交易日已经归档的机构、营业部及其他有效席位记录，不用缺失结果覆盖历史。

## 本地副本和测试

不要直接拿真实 `.local-data/runtime/` 做实验。测试时使用临时运行目录：

```bash
DASHBOARD_HOME=/tmp/niuone-smoke DASHBOARD_PORT=8877 ./scripts/run_standalone.sh
```

提交前运行：

```bash
./scripts/validate.sh
git status --ignored --short
```

`.local-data/` 应显示为 ignored，不应出现在 staged files 中。

## 发布和备份

本机部署脚本会把当前 `app/`、环境文件和启动脚本备份到：

```text
.local-data/backups/
```

备份目录同样属于私有数据区域，不应提交或外传。回滚时优先从备份恢复 `app/`，或使用 `git revert` 做非破坏性提交回滚。

## 处理疑似泄露

如果模型密钥、本地凭据或数据库误入公开位置：

1. 立即撤销或轮换对应密钥或凭据。
2. 从代码和文档中删除泄露内容。
3. 检查 `git status --ignored --short` 和最近提交。
4. 未配置管理员密码时，必要时重建 `.local-data/runtime/dashboard_admin_token.txt`；按需重建相关数据库。
5. 对已经推送到远端的敏感内容，按远端平台的泄露处理流程清理历史。
