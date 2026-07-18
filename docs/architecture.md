# 工程分层

## 服务端

```text
src/server/app/
├── api/             # API 路由装配和版本入口
├── common/          # 无框架依赖的异常、分页和共享类型
├── core/            # 配置与日志初始化
├── db/              # 异步 Web 会话、同步调度会话、ORM Base
├── integrations/    # Tushare 等外部系统适配器
├── models/          # Alembic 模型注册入口
├── modules/         # stocks/tasking/operations/system 业务模块
├── observability/   # Prometheus 指标和 HTTP 观测中间件
└── scheduler/       # 独立 APScheduler 进程
```

业务模块采用 `api → service → repository → models` 单向依赖。外部数据源必须经
`integrations` 中的协议访问，不能把 Tushare SDK 类型传入业务层。

数据源限流也位于 `integrations`：任务代码只表达“需要哪些数据”，不负责计算请求间隔。
所有 Tushare 实例共享同一个进程级限流器，定时任务、手动补数和网络重试使用同一预算。

FastAPI 和 APScheduler 分进程运行。FastAPI 负责管理接口，APScheduler 负责扫描和执行任务；
两者都使用 PostgreSQL，但使用独立 SQLAlchemy 会话工厂。APScheduler 3.x 通过 PostgreSQL
advisory lock 强制只启动一个实例。

## Web

```text
src/web/src/
├── api/             # Axios 客户端与统一错误转换
├── components/      # 通用组件与图表封装
├── layouts/         # 管理端布局
├── modules/         # dashboard/tasking/operations/system 功能模块
├── plugins/         # ECharts 等第三方库注册
├── router/          # 路由装配
├── stores/          # 跨页面全局状态
├── styles/          # 设计变量与基础样式
├── types/           # 跨模块类型
└── utils/           # 可独立测试的纯函数
```

页面按功能模块组织，避免项目增长后出现单个巨大 `views/`、`api/` 或 `store/` 目录。
页面不能直接调用 Axios；模块 API 负责传输，Store 只保存确实需要跨页面共享的状态。
