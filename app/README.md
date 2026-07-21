# app 目录约定

`app/` 根目录只保留包声明和本说明文件。可执行入口统一位于 `entrypoints/`，历史裸模块适配器统一位于 `compat/`，实际实现按领域存放：

| 领域目录 | 实现内容 |
|---|---|
| `automation/` | Cron 规则与调度服务 |
| `compat/` | 历史裸模块名适配器，不承载业务实现 |
| `core/` | 路径、缓存等基础设施 |
| `dashboard/` | Dashboard 服务、公开投影、版本化快照、API 与安全辅助 |
| `entrypoints/` | Dashboard、调度器、监控与报告等可执行入口 |
| `market_data/` | 行情访问、证券代码规范化与问财研究数据客户端 |
| `messaging/` | 通知模型、渠道、分发与兼容层 |
| `monitoring/x/` | X 监控服务、解析、格式化与状态规则 |
| `reports/a_share/` | A 股竞价、午盘、盘后、日历与 Grok 报告 |
| `reports/us/` | 美股盘面和机构评级报告 |
| `screening/` | 多策略选股与候选增强 |
| `storage/` | 推送历史、模拟盘数据库和报告存储 |
| `strategies/` | 策略注册、评分、归因与退出规则 |
| `trading/` | 模拟交易服务、优化器和卖出信号 |

`entrypoints/` 中的脚本是项目支持的启动路径。`compat/` 通过 `_compat.py` 在历史模块命名空间中运行迁移后的实现，供内部裸模块导入和迁移期集成使用。新增业务代码不得写入 `entrypoints/` 或 `compat/`。

Dashboard 继续由 `niuone_dashboard.py` 单端口启动并保留原页面布局。`dashboard/fastapi_app.py` 只负责 FastAPI/Uvicorn 应用组合、中间件、Vue 构建和共享缓存响应，具体 HTTP 接口按 system、messages、market、practice、admin 拆在 `dashboard/routers/`。旧 `BaseHTTPRequestHandler`、`ThreadingHTTPServer` 及原生 HTML/JavaScript 控制器已经删除。`dashboard/security.py`、`dashboard/visit_stats.py` 和 `dashboard/response_cache.py` 分别承载管理员访问控制、访问统计和并发响应缓存，`dashboard/server.py` 只保留这些能力的兼容委托以及后台状态、配置与数据源组合，不再定义 HTTP 路由。浏览器展示模型由 `dashboard/public_projection.py` 构建，并由 `dashboard/public_snapshots.py` 原子发布；交易和外部请求不得下沉到前端。
