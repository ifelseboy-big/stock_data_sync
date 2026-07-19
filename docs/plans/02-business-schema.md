# 业务数据表与分区

状态：已完成

## 目标

按照 `docs/data-model` 落地 25 张业务表、单位约束、外键、访问索引及 6 张按交易日月分区的事实表。

## 实现任务

1. 按股票、专题、指数和 ETF 模块建立 SQLAlchemy 模型。
2. 明确金额、成交量、百分比、交易所和源字段的 Python 类型转换边界。
3. 新增 Alembic 迁移，创建主表、当前月及未来 3 个月分区和全部确认索引。
4. 实现幂等分区管理服务，支持启动检查和历史回填前预建分区。
5. 增加模型约束、分区边界和迁移升级/回滚测试。

## 验收条件

- 25 张业务表与设计文档逐字段核对通过。
- 6 张事实表不存在 DEFAULT 分区，唯一键包含 `trade_date`。
- 分区维护重复执行不产生重复对象。
- 典型日期查询具备分区裁剪和对应访问索引。

## 完成记录

- 完成日期：2026-07-19。
- 已按股票、专题、指数和 ETF 边界建立 25 张业务表及全部确认索引。
- `stock_daily`、`stock_technical_daily`、`stock_moneyflow_daily`、`market_theme_member_daily`、`etf_daily` 和 `etf_share_size_daily` 按 `trade_date` 月分区，不设置 DEFAULT 分区。
- 迁移创建当前月及未来 3 个月的 24 个分区；Scheduler 启动时及每天 08:30 检查当前月和未来 3 个月分区。
- 已通过 Ruff、mypy、58 个服务端测试、空库 PostgreSQL 升级和 Alembic 无漂移检查；ETF 两张日表已用真实数据验证分区写入与按月裁剪。
