# NiuOne repository instructions

本文件适用于仓库根目录及其全部子目录。若某个子目录以后新增更具体的
`AGENTS.md`，以更深层文件的规则为准。

## 工程目标

NiuOne 是一个本地优先的市场研究、自动化监控和模拟交易工程，覆盖 A 股、美股、
Dashboard、定时任务、消息通知与策略研究。修改时优先保证：

1. 交易与账户数据正确，不用不完整信息重写真实记录。
2. 外部数据源失败时可降级、可重试且有明确超时，不让后台任务无限阻塞。
3. macOS、Linux、Windows 和容器入口保持兼容。
4. 历史模块导入、运行时 monkeypatch 和本地部署升级保持兼容。
5. 变更范围聚焦，不顺手修改无关策略、阈值、仓位或风控规则。

开始修改前，先阅读与任务直接相关的源码和测试。涉及模块边界、运行数据或部署时，
同时阅读：

- `app/README.md`
- `docs/APP_ARCHITECTURE.md`
- `config/runtime-policy.md`
- `scripts/validate.sh`

## 目录与依赖边界

- `app/entrypoints/` 是受支持的启动入口，只做路径初始化和启动委托。
- `app/compat/` 是历史裸模块名适配层，只保留兼容逻辑，不新增业务实现。
- 新业务代码放入对应领域包：`dashboard`、`market_data`、`messaging`、`monitoring`、
  `reports`、`screening`、`storage`、`strategies` 或 `trading`。
- `app/` 根目录只允许保留 `__init__.py` 和说明文档，不新增零散 Python 模块。
- 依赖方向为“入口/兼容层 -> 领域包 -> 标准库与外部数据源”；领域包不得反向导入
  根入口。
- 可复用计算、解析和规则优先做成接收显式参数的领域函数；进程锁、缓存、路径、环境
  配置和服务编排留在组合层。
- 修改已迁移模块时，保留 `app.<domain>` 与历史裸模块两种导入方式共享同一模块状态的
  语义。不要复制注册表、缓存或单例来绕过兼容层。

## Python 与运行时风格

- 支持 Python 3.11 及以上版本；新代码使用清晰的类型标注和标准库类型。
- 路径使用 `pathlib.Path` 和现有 `app/core/paths.py` 约定，不硬编码用户目录、仓库绝对
  路径或平台专用分隔符。
- 文本和 JSON 文件显式使用 UTF-8。需要持久化的重要状态时优先采用临时文件加原子替换，
  并保持重复执行幂等。
- 时间、交易日和交易时段逻辑复用现有 helper；A 股面向用户的时间默认按北京时间处理，
  不混用无时区的 UTC 值。
- 外部 HTTP、模型和行情请求必须设置有上限的超时、重试次数和并发数。并发访问共享缓存
  或文件时必须使用锁，避免 check-then-set 竞态和重复请求风暴。
- 除非任务明确要求，不新增第三方依赖。若确需新增，更新 `requirements.txt`，并说明为何
  标准库或现有依赖无法满足。
- 不用宽泛异常静默掩盖新错误。允许降级的路径应保留可诊断的错误类型或状态，同时避免把
  密钥、完整响应和用户数据写入日志。
- 修复恢复、补齐或迁移逻辑时，真实记录优先于重建数据；先定义安全边界，再追加缺失内容，
  不覆盖边界之前的历史。

## 配置约定

- 配置优先级保持为显式环境变量、本地 Dashboard 配置、代码默认值；复用现有读取和规范化
  helper，不另建平行配置系统。
- 新增用户可配置项时，按影响范围同步检查：
  - `ENV_CONFIG_SCHEMA`、输入规范化和服务端验证；
  - `dashboard.env.example`；
  - 管理设置页的控件和说明；
  - `docs/OPERATIONS*.md`、`docs/STANDALONE*.md` 等相关中英文文档；
  - 配置生效时机：`runtime`、`next_run` 或 `restart`。
- 默认值必须保持旧部署可用。会改变模型接口、交易决策、策略阈值或通知行为的开关，默认
  应优先兼容现有部署，并提供显式覆盖方式。
- 密钥类配置只能保存在受控环境变量或 `.local-data/` 私有文件中，不写入示例、测试夹具、
  日志、issue 或 PR 描述。

## 前端与用户文案

- `web/` 使用 Vue 3、Vue Router 与 Vite；`frontend/` 仅保留 Vue 组件复用的页面样式，不再新增原生 HTML 或控制器脚本。
- 修改 API 字段时同步检查 Dashboard 与管理页消费者、缓存键和缓存失效逻辑。
- 用户界面保持当前中文术语和标签风格；修改公开说明时同步维护对应的英文 README 或文档。
- JavaScript 修改至少运行受影响文件的 `node --check`；Vue 组件或构建配置修改必须运行
  `pnpm --dir web run build`。

## 测试与验证

- 测试使用现有 `unittest` 风格，文件命名为 `tests/test_*.py`。
- 每个行为修复至少增加一个能在修复前失败的回归测试。涉及并发、恢复或缓存时，同时覆盖
  重复执行、边界时间、已有真实数据和失败降级。
- 测试不得依赖真实网络、真实模型密钥或真实 `.local-data/runtime/`。使用 mock、临时目录和
  固定时间；运行目录使用 `/tmp/niuone-*` 或 `tempfile`。
- 先运行最小相关测试，例如：

  ```bash
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_sell_strategy_rules.py'
  ```

- 完成后运行全量验证：

  ```bash
  ./scripts/validate.sh
  ```

- 全量验证包括 Python、JavaScript、Shell、Windows BAT 和全部单元测试。若本机环境导致
  既有失败，必须在未修改的 `main` 上复现并在交付说明中明确列出；不要删除断言或跳过测试
  来制造通过结果。
- 文档或极小配置变更至少运行 `git diff --check`，并说明为何未运行全量测试。

## 运行数据与安全

- `.local-data/`、数据库、日志、备份、状态文件和本机配置均为私有运行数据，不提交、不复制
  到文档，也不在工具输出中展示。
- 不要直接用真实运行目录做实验。临时启动示例：

  ```bash
  DASHBOARD_HOME=/tmp/niuone-smoke DASHBOARD_PORT=8877 ./scripts/run_standalone.sh
  ```

- 提交前检查：

  ```bash
  git status --ignored --short
  ```

- 不提交 `.env`、`dashboard.env`、`*.db`、`*.key`、`*.token`、`*.secret`、日志或备份。
- 发现疑似泄露时先停止扩散并轮换凭据，再按 `config/runtime-policy.md` 清理。

## Git、提交与 PR 风格

- 提交和 PR 标题使用 Conventional Commits：`type(scope): subject`。
- 常用 `type`：`feat`、`fix`、`refactor`、`perf`、`docs`、`test`、`chore`。
- `scope` 取主要领域，例如：`dashboard`、`market`、`settings`、`strategy`、`trading`、
  `auction`、`container`、`run`、`readme`、`app`。修改 `app/trading/` 的行为应使用
  `fix(trading): ...`，不要退化成无 scope 的 `fix: ...`。
- `subject` 使用简洁的英文祈使表达，小写开头，不加句号。一个提交只承载一个逻辑变更。
- PR 标题必须直接可用作 squash commit 标题；执行 squash merge 前再次核对最终标题，避免
  把不合规的 PR 标题写入 `main`。
- PR 描述至少包含：修改内容、问题原因、兼容性/影响范围、实际运行的验证命令和结果。
- 合并前基于最新 `main` 检查冲突。解决冲突时按语义整合双方功能，不直接选择整文件的
  ours/theirs，尤其注意新策略注册、共享上下文、配置 schema 和测试数量变化。
- 默认使用 squash merge 保持主分支单提交、线性历史。不删除远端分支，除非维护者明确要求。
- 禁止改写或强推 `main`；只有维护者明确要求修复历史时，才允许使用带精确旧 SHA 的
  `--force-with-lease`，并在推送前确认树和父提交未意外变化。

## 完成交付检查

交付前确认：

1. `git diff` 仅包含任务范围内的改动，没有覆盖用户已有修改。
2. 新代码位于正确领域，入口和兼容层仍保持薄层。
3. 行为、兼容入口和失败边界已有测试。
4. 配置示例、管理页和中英文文档按需同步。
5. `git diff --check` 及相关/全量测试结果已记录。
6. 没有运行数据、密钥、数据库或日志进入待提交文件。
7. 提交或 PR 标题符合 `type(scope): subject`，scope 与主要改动一致。
