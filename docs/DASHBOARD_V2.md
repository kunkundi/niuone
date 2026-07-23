# Dashboard 增量展示与部署

简体中文 | [English](DASHBOARD_V2_EN.md)

Dashboard 已迁移到 Vue 3 + Vite 与 FastAPI/Uvicorn，同时保持原页面布局、栏目和交互不变。公开页面与受管理员密码保护的 `/admin` 共用一个进程和端口；性能优化集中在服务端快照、条件请求、按需加载和 CDN 缓存，不把交易或行情计算下沉到浏览器。

## 架构

```text
行情 / 模型 / 定时任务 / 模拟交易
              │
              ▼
 FastAPI / Uvicorn 单进程（默认 0.0.0.0:8787）
   ├── Vue 3 公开页面与 /admin
   ├── 原生增量快照 API
   ├── 进程内旧 API 兼容层（无第二端口）
   └── 后台展示投影（默认每 15 秒）
              │
              ▼
 $DASHBOARD_HOME/public-data/
   ├── latest.json
   ├── manifests/<revision>.json
   └── objects/<sha256>.json
```

浏览器每 15 秒先检查 `/api/v2/public/latest`。该指针通常不足 300 字节，并支持 `ETag` 与 `304 Not Modified`；版本变化时再读取 manifest，只对摘要发生变化的数据区块调用相应接口。模拟账户首屏只读取快速快照；完整历史仅在用户打开缺少分时数据的交易日历日期时按需读取，成功后本页面会话不再重复下载，失败时最多每 5 分钟重试一次。

服务端投影由 `app/dashboard/public_projection.py` 的字段白名单构建，通过 `app/dashboard/public_snapshots.py` 原子发布。浏览器请求不会触发交易执行、模型调用或完整历史重算。

Vue 源码位于 `web/`，Vite 生产构建输出到忽略提交的 `web/dist/`。macOS/Linux/Windows 启动器会在源码变化或产物缺失时自动运行锁定版本的 pnpm 构建；Docker 在独立 Node 构建阶段生成前端，最终运行镜像不包含 Node。主题切换、合规弹窗、版本状态、栏目 bootstrap 与 Vue Router、最后刷新时间、全部公开栏目、完整模拟账户和整个管理页均已由 Vue 组件独立管理。龙虎榜组件独立负责按日期请求、60 秒最新数据刷新、排序、明细展开和栏目计数；指数行情拆分为数据 composable、分时图、指数分组和 A 股/美股行情组件，按 15 秒刷新主数据、按 60 秒刷新资金流，并顺序优先绘制指数后并行读取辅助榜单。资金流动子路由也已拆为 60 秒数据刷新层和独立动画层，保留真实采样点插值、左右净流入/流出排序、播放、暂停、重播、拖动与倍速，并在离开组件时取消请求和动画帧。盘面监控拆为数据缓存、交易日翻页、报告卡片、结构化详情和隔夜美股摘要组件：页面首次读取最多 200 条历史，之后每 15 秒只读取 `/api/messages/revision` 的记录数与最新记录指纹，只有指纹变化才重新读取历史；隔夜美股摘要独立按 5 分钟刷新。美股机构评级拆为独立数据缓存、日期翻页、评级表格和展开详情组件：首次读取最多 120 条历史，之后每 10 分钟只检查轻量修订摘要，修订变化才重新读取历史；当前日期的实时股价按需读取，公司行业资料只在用户展开对应股票时读取。推特监控拆为分页数据缓存、当前页指纹检查、相邻页预取、推文行、线程详情、媒体画廊和图片查看器：每页保持 10 条和 5 分钟会话缓存，每 15 秒只读取当前页的轻量哈希，只有记录、上下文或媒体元数据变化时才重新下载该页；翻页或离开组件时会取消未完成的图片请求。模拟交易已拆为账户数据 composable、账户概览、持仓/卖出卡片、收益曲线、交易日历、操作日志、规则、盘面总结和候选股组件；账户与候选股共享公开投影订阅，只有对应区块摘要变化时才重新请求数据。生产页面不再加载旧 Dashboard 控制器，栏目 bootstrap、深链和切换均由 Vue Router 管理。管理页覆盖登录、设置首页、客户端路由、所有分组字段、保存与脏状态、离开确认、通知渠道增删/启停，以及问财、模型和通知连接测试。FastAPI 直接处理版本/bootstrap、消息及其轻量修订摘要、龙虎榜、X 图片代理、模拟交易读模型与受保护的刷新/恢复/盘面总结操作、状态报告、自优化应用、指数、板块、热门股、行业资金流、美股行情辅助数据和基准数据等路由，也原生负责管理员登录、配置读取与保存，以及问财、模型和通知连接测试。所有浏览器 API 都由 FastAPI 显式声明，未知路径直接返回 404，不再转交旧 `BaseHTTPRequestHandler`；Vue 管理页也不加载 `frontend/admin.js`。

指数行情页在“资金流动”右侧提供独立的“市场情绪”切换按钮。该视图展示 60 秒真实采样的 A 股市场情绪曲线：上方双纵轴呈现涨停、跌停、炸板、红盘和绿盘数量，下方共享时间轴的量能区间同时呈现预测全天量能、当前实际累计量能，以及预测全天量能相对上一交易日全天成交额的有符号增量（亿元）。实际量能优先取东方财富沪深指数 1 分钟成交额，腾讯全市场成交额兜底；预测值使用东方财富最近 20 个完整交易日的 5 分钟累计成交占比中位数反推。接口同时返回实际来源、模型、样本起止日期和样本数，页面在量能区下方展示这些口径。对应的 FastAPI 接口为 `/api/market_breadth`，深链接使用 `/indices?panel=market-breadth`。

Vue 资金流请求使用 `compact=1` 字段投影，只传输动画所需的采样节点。

## 管理页安全

`/admin` 可以和公开页面使用同一个公网域名。页面本身可打开；FastAPI 原生的登录、配置读取与保存、问财/模型/通知连接测试接口都要求相应的管理员凭据或 Cookie。修改和测试请求还要求 `X-NiuOne-Action`，并受请求体上限、管理员接口限流及各测试接口的独立限流保护。应设置强管理员密码，并保持 Cloudflare、反向代理和源站使用 HTTPS。

## 运行

```bash
./run.sh
```

开发环境也可以分别运行 Vite 热更新与 FastAPI；`5173` 只用于本机开发，生产环境仍只有 `8787`：

```bash
pnpm --dir web install --frozen-lockfile
pnpm --dir web dev
./run-dashboard.sh
```

打开：

- 公开页面：<http://127.0.0.1:8787/>
- 管理页面：<http://127.0.0.1:8787/admin>

主要配置：

| 配置 | 默认值 | 生效时机 |
|---|---:|---|
| `DASHBOARD_PUBLIC_PROJECTION_ENABLED` | `1` | 重启 |
| `DASHBOARD_PUBLIC_REFRESH_SECONDS` | `15` | 重启 |
| `DASHBOARD_PUBLIC_DATA_DIR` | `$DASHBOARD_HOME/public-data` | 重启 |

## 公网与缓存

Cloudflare Tunnel、Nginx 或 Caddy 只需代理 `8787`。建议：

- `/assets/*`：缓存 Vue/Vite 内容哈希产物一年；
- `/api/v2/public/objects/*`、`/api/v2/public/manifests/*`：一年 immutable；
- `/api/v2/public/latest`：边缘缓存 5 秒并允许 30 秒 stale-while-revalidate；
- `/admin*`、`/api/admin/*` 和写操作：绝不缓存；
- 其他 API：遵循服务端 `Cache-Control`，不要用一条全站规则强制公开缓存。

生产 HTTP 接口已按 system、messages、market、practice、admin 拆入 `dashboard/routers/`。旧 `BaseHTTPRequestHandler`、`ThreadingHTTPServer`、原生 HTML 与 Dashboard/Admin JavaScript 控制器均已删除。

如果家庭上行或 Tunnel 路径仍是瓶颈，可把服务部署到靠近访问者的云主机，再由 CDN 缓存静态资源和增量快照。迁移或回滚只改变运行位置和域名回源，不重建账户、交易或消息历史。
