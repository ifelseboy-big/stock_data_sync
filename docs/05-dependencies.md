# 技术依赖说明

## 服务端

- FastAPI：HTTP API 与 OpenAPI 文档。
- SQLAlchemy / Alembic / Psycopg：PostgreSQL ORM、迁移和驱动。
- APScheduler 3.x：独立定时调度进程，JobStore 使用 PostgreSQL。
- Tushare：A 股等金融数据 SDK，仅在适配器层使用。
- PyArrow：流式写入和读取 Parquet、生成结构指纹及批量转换；原始资产层实现前必须加入 `pyproject.toml` 和 `uv.lock`。
- 内建平滑限流器：按 480 次/分钟均匀发送请求，为 500 次硬限制保留余量。
- Tenacity：仅对网络异常执行指数退避重试，每次重试重新占用请求额度。
- Prometheus Client：HTTP、任务和数据源调用指标。
- Structlog：结构化 JSON 日志。
- Pydantic Settings：环境变量和配置校验。

## Web

- Vue 3 / Vue Router / Pinia：视图、路由和全局状态。
- Element Plus：管理端组件库，采用按需导入。
- Axios：HTTP 客户端和统一错误转换。
- Apache ECharts / vue-echarts：运行趋势、耗时和成功率图表。
- VueUse：通用 Composition API 能力。
- Day.js：日期格式化和时间运算。
- Vite / TypeScript：开发服务器、构建和静态类型。
- Vitest / Vue Test Utils / happy-dom：单元测试和组件测试。

版本由 `uv.lock` 和 `package-lock.json` 固定；升级依赖时必须同时运行完整检查和构建。

业务层不使用 pandas DataFrame 作为跨层契约。Provider 返回源记录或 Arrow 批次，原始资产存储负责 Parquet，处理器再把明确类型的数据写入临时表。
