# A股数据平台开发文档

本目录是供开发直接使用的本地设计基线。内容拆自两份已确认的飞书设计文档，未改写表字段、接口依赖和运行语义；每个源章节只保留一份，文件头记录了来源与修订版。

## 文档来源

| 来源 | 修订版 | 本地内容 |
|---|---:|---|
| [数据库设计与Tushare接口调用流程](https://tcnq6fudd3wh.feishu.cn/docx/NJXndRs7eoRq7KxWPQ0cCyycnPc) | 78 | 表结构、字段含义、分区索引、接口依赖、调用时间线、补采规则 |
| [数据库、采集、调度、加工、入口与监控详细设计](https://tcnq6fudd3wh.feishu.cn/docx/WoYqdWMeJoqcOtxUVJxccyjenTe) | 88 | 单机架构、工程落地、状态机、发布、管理入口、内建监控、恢复与验收 |

## 固定设计基线

- 部署目标是Mac mini单机原生进程，不使用容器；监控由现有API、Scheduler和管理端内建，不引入独立监控组件。
- 正式库共31张表：25张业务表、6张系统运行表。股票、ETF和指数分开建模。
- `stock_daily`合并`daily`、`daily_basic`和`adj_factor`，只从`stk_limit`补充`up_limit`、`down_limit`。
- `stock_daily`、`stock_technical_daily`、`stock_moneyflow_daily`、`market_theme_member_daily`按`trade_date`按月分区。
- Tushare原始结果保留为不可变Parquet资产；正式业务消费者只读取PostgreSQL发布数据。
- APScheduler只负责触发计划，任务与发布状态以PostgreSQL中的运行表为准。
- `dataset_release`表示完成性、血缘和当前加工结果，不是可直接切换的历史版本回滚表。

## 开发阅读顺序

1. 先读[范围与全局约定](00-overview-and-conventions.md)和[架构与部署](01-architecture-and-deployment.md)。
2. 数据库迁移按[系统运行表](02-system-runtime-tables.md)、[股票表](03-stock-tables.md)、[重点业务表](04-focus-tables.md)、[指数与ETF表](05-index-and-etf-tables.md)、[数据库工程约束](06-database-implementation.md)实现。
3. 数据链路按[原始数据与Parquet](07-raw-data-and-parquet.md)、[Tushare接口流程](08-tushare-interface-workflow.md)、[采集实现](09-data-acquisition.md)实现。
4. 运行闭环按[调度](10-scheduling.md)和[加工与发布](11-processing-and-release.md)实现。
5. 管理能力按[管理API](12-management-api.md)和[监控与运维](13-monitoring-and-operations.md)实现。
6. 上线前执行[恢复、备份与安全](14-recovery-backup-security.md)和[工程实施与验收](15-engineering-and-acceptance.md)。

## 冲突处理顺序

业务表名、字段、类型和字段含义以`02`至`05`为准；系统运行表的补充字段、约束和事务语义以`06`为准；接口日期、依赖关系和完整性判定以`08`为准；进程、状态机和运维实现以`01`、`09`至`15`为准。实现中不得自行合并股票、ETF、指数表，不得省略原始资产、依赖解析或发布完整性步骤。
