# 文档中心

本目录只维护一套有效设计。顶层文档描述系统级规则，`data-model/` 只描述数据库表和原始资产，不再重复架构、调度、采集或部署内容。

## 已确认的设计基线

- 后端采用 Python 3.12、uv、FastAPI、SQLAlchemy、Alembic、APScheduler 和 PostgreSQL 18；前端采用 Vue 3、TypeScript、Vite 和 Element Plus。
- 采集任务只负责调用 Tushare 并封存不可变 Parquet；加工任务依赖一个或多个已封存资产，负责清洗、聚合、校验和正式入库。
- 采集批次关闭后统一生成加工计划；全部加工任务共用一个受控并发入口，默认最多 3 个任务并行，同一输出数据集保持串行。
- Tushare 账户限制为 500 次/分钟，应用预算为 480 次/分钟；所有采集来源共享同一额度。
- PostgreSQL 是任务状态、队列、依赖和发布的唯一事实来源，不引入 Redis、Kafka、Celery 或 Airflow。
- 生产环境为 Mac mini 原生进程，由 `launchd` 管理。首次安装必须由用户分别指定主程序目录和数据目录；正式标签源码在目标 Mac 本地构建，普通升级只切换程序。

## 阅读顺序

| 顺序 | 文档 | 解决的问题 |
| ---: | --- | --- |
| 1 | [系统架构与工程分层](01-system-architecture.md) | 系统有哪些模块，代码如何分层，进程如何划分 |
| 2 | [任务调度与数据加工流程](02-task-workflow.md) | 采集批次、依赖、受控并发加工、状态和重试如何运行 |
| 3 | [Tushare 采集设计](03-tushare-collection.md) | 接口范围、调用时间、限流、拆分、完整性和能力边界 |
| 4 | [管理后台与可观测性设计](04-admin-console.md) | 运维页面、指标口径、管理 API 和告警规则 |
| 5 | [技术依赖说明](05-dependencies.md) | 引入哪些库以及它们承担的职责 |
| 6 | [Mac mini 安装、升级与运行](06-deployment.md) | 源码安装、程序升级、doctor、配置和服务管理 |
| 7 | [数据模型](data-model/README.md) | PostgreSQL 表结构、索引分区、写入规则和 Parquet 资产 |

## 维护规则

- 同一规则只在一个主题文档中定义，其他文档使用链接引用。
- 顶层编号表示推荐阅读顺序，不表示实现阶段。
- 文档描述目标设计；尚未实现的能力必须明确标注，不能把页面占位或接口草稿写成已完成。
- 数据库表名、字段、类型和单位以 `data-model/` 为准；任务状态和执行语义以 `02-task-workflow.md` 为准；接口规则以 `03-tushare-collection.md` 为准。

具体实施拆解统一放在[实现规划目录](plans/README.md)。

## 设计来源

现有数据模型和接口规则融合自以下确认稿，冲突项已经按本项目已确认的架构基线统一处理：

- [数据库设计与 Tushare 接口调用流程](https://tcnq6fudd3wh.feishu.cn/docx/NJXndRs7eoRq7KxWPQ0cCyycnPc)，修订版 78。
- [数据库、采集、调度、加工、入口与监控详细设计](https://tcnq6fudd3wh.feishu.cn/docx/WoYqdWMeJoqcOtxUVJxccyjenTe)，修订版 88。
