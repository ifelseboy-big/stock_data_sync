# Server

FastAPI 后端服务。配置从项目根目录 `.env` 读取，数据库仅使用 PostgreSQL。

Web API 和调度器是两个独立进程：

```bash
uv run uvicorn app.main:app --reload
uv run python -m app.scheduler.runner
```

APScheduler 只允许启动一个实例。其持久化 JobStore 与业务运行记录均使用 PostgreSQL，
不依赖 Redis。

Tushare 外部限制为 500 次/分钟，应用默认预算为 480 次/分钟。限流在数据源适配层统一执行，
并覆盖失败重试；请求超时默认 30 秒，只对网络异常做指数退避重试。
