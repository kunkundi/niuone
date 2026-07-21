# app 模块结构

`app/` 根目录不再放业务实现或零散入口。受支持的命令行/服务入口集中在 `app/entrypoints/`，历史裸模块适配器集中在 `app/compat/`，其余子包承载领域实现。兼容适配器由 `_compat.py` 在历史模块命名空间中执行迁移后的实现，因此运行时 monkeypatch 语义保持不变。

## 领域边界

| 目录 | 职责 | 不应承担的职责 |
|---|---|---|
| `app/core/` | 运行路径策略、原子 JSON 缓存等跨领域基础设施 | 业务规则、服务编排 |
| `app/automation/` | 定时任务模型、Cron 匹配与时间配置规则 | 信号处理、子进程执行和调度器状态 |
| `app/dashboard/` | FastAPI 路由、安全辅助、公开展示投影、版本化快照、API 缓存编排和榜单规则 | 交易决策、账户文件重建 |
| `app/market_data/` | 行情访问和证券代码规范化工具 | 策略决策、交易状态 |
| `app/messaging/` | 通知模型、渠道适配、HTTP 传输、分发和成交消息格式 | 交易状态持久化 |
| `app/monitoring/x/` | X 关注列表、媒体/上下文解析、消息格式、时间与重试状态规则 | 网络抓取、进程循环和消息入库 |
| `app/reports/a_share/` | A 股报告共用的数值、代码、行业、日历、Grok 提示词/解析和超时工具 | 定时任务入口、数据源编排 |
| `app/storage/` | 报告记录构造、消息 ID/去重规则和显式存储接口 | 数据库路径和进程级连接状态 |
| `app/screening/` | 多策略扫描和候选行业增强 | 账户执行、HTTP 路由 |
| `app/strategies/` | 策略注册、评分、归因、选股、退出规则和提示词片段 | 行情 I/O、账户执行 |
| `app/trading/` | 模拟交易中的纯计算能力，例如卖出技术信号 | 账户文件、网络请求和成交落盘 |

`entrypoints/` 中的 Dashboard、交易器、调度器、监控器和报告入口均为薄启动器；`compat/` 中的各 `*_dashboard_api.py`、`notifications.py` 及历史模块名均为薄适配器。Dashboard 的生产 HTTP 组合层位于 `dashboard/fastapi_app.py`，接口实现按领域位于 `dashboard/routers/`；`dashboard/server.py` 只保留后台状态、配置和数据源组合函数。其他实际组合实现位于 `trading/practice_trader.py`、`automation/scheduler_service.py`、各领域的 `*_service.py` 等文件。

组合层的正式执行合同是直接运行 `app/entrypoints/*.py`。领域实现使用 `app.<domain>` 包路径；仍依赖历史裸模块名的组合代码由入口统一加载 `app/compat/`，外部代码不应再依赖已经移除的 `app/*.py` 路径。

## 依赖方向

```text
启动脚本 / 兼容入口
        ↓
领域包（core、automation、dashboard、messaging、monitoring、reports、storage、strategies、trading）
        ↓
标准库与外部数据源
```

领域包不能反向导入根入口。进程锁、缓存、文件路径、运行时配置等可变状态由组合层持有；领域函数优先接收显式参数。这样既能独立测试，又能保留调用方对旧模块全局值进行替换的兼容行为。

## Dashboard Web 与增量读模型

Dashboard 使用 Vue 3 + Vite 和 FastAPI/Uvicorn，并保持单进程、单监听端口及原页面布局。高频展示读取与服务端计算解耦：

- `public_projection.py` 只接受显式源数据，并用字段白名单生成展示模型；
- `public_snapshots.py` 原子发布内容寻址对象、manifest 和 latest 指针；
- `projection_service.py` 在后台固定频率读取服务端状态，浏览器轮询不会触发交易、行情或历史重算；
- `fastapi_app.py` 是唯一 HTTP 监听者，只组合中间件、Vue 构建、共享缓存响应和领域路由；
- `routers/` 显式声明 system、messages、market、practice、admin 五组浏览器接口；
- `security.py`、`visit_stats.py`、`response_cache.py` 接收显式状态和路径，分别实现访问控制、统计持久化和带失效代次的并发 JSON 缓存；
- `server.py` 的既有后台函数仍由 FastAPI 组合层复用，但其中已不存在 `BaseHTTPRequestHandler`、`ThreadingHTTPServer` 或静态页面分发；
- `web/` 保存 Vue 组件、Vite 配置和依赖锁，生产产物由 FastAPI 从 `web/dist/` 提供；
- Vue 已接管主题、合规弹窗、版本状态、栏目 bootstrap 与路由、最后刷新时间、全部公开栏目、完整模拟账户及管理页；旧 `frontend/*.html`、`frontend/dashboard.js` 和 `frontend/admin.js` 已删除，`frontend/` 仅保存 Vue 复用的样式。模拟账户拆为账户概览、持仓/卖出卡片、收益曲线、交易日历、操作日志、规则、盘面总结和候选股组件，数据层共享公开投影订阅并按区块摘要刷新。FastAPI 显式声明全部浏览器 API，未知路径直接返回 404；管理员会话、操作请求头、请求体上限与分级限流语义保持不变。
- 浏览器先检查轻量 latest 指针，只在区块摘要变化时加载对应数据；完整模拟账户历史仅在用户打开缺少分时数据的日历日期时按需读取，成功后本页面会话不再重复下载，失败最多每 5 分钟重试一次。
- Vue 资金流动画请求使用 `compact=1` 字段投影，服务端仅返回节点标识、名称、净额、采样时间和控件配置；完整响应仍保留给显式请求它的 API 客户端。

`/admin` 可以与公开页面通过同一域名访问，但所有配置读取、修改和测试操作仍必须经过管理员会话、限流与操作请求头校验。

## 变更检查

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
./scripts/validate.sh
```

新增功能应优先放入对应领域包；只有 CLI、HTTP 路由、调度或跨域编排代码留在根入口。
