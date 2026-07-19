# 专题与指数数据同步链路

状态：已完成

## 目标

补齐分类、热榜、题材、龙虎榜、涨跌停、板块资金和指数的正式同步链路。所有接口必须复用统一采集状态机、480/min 全局请求预算、不可变 Parquet、批次关闭、依赖解析、全局串行加工、原子发布和运维接口，不建立旁路脚本或第二套回补流程。

## 接口与输出

| 业务 | Tushare 接口 | 输出数据集 |
| --- | --- | --- |
| 同花顺分类 | `ths_index`、`ths_daily`、`ths_member` | `concept_board`、`concept_board_daily`、`concept_board_member` |
| 热榜 | `ths_hot`、`dc_hot` | `stock_hot_rank_daily` |
| 东方财富题材 | `dc_concept`、`dc_concept_cons` | `market_theme_daily`、`market_theme_member_daily` |
| 龙虎榜 | `top_list`、`top_inst` | `stock_top_list_daily`、`stock_top_inst_daily` |
| 涨跌停和连板 | `limit_list_d`、`limit_step` | `stock_limit_event_daily`、`stock_limit_step_daily` |
| 板块资金 | `moneyflow_cnt_ths`、`moneyflow_ind_ths` | `ths_board_moneyflow_daily` |
| 指数 | `index_basic`、`index_daily`、`index_dailybasic`、`index_weight` | `market_index`、`market_index_daily`、`index_daily_basic`、`market_index_weight` |

## 实现任务

1. 为 17 个接口声明字段结构、自然键、空结果、行数上限、重试、发布时间、请求范围和拆分方式。
2. 月度主数据先发布概念和指数主表，再根据正式主表动态生成概念成员及指数权重请求。
3. 每日任务发布题材主表后，再按当日全部题材代码生成成员采集任务；成员任务不能与主表采集同时盲目展开。
4. 热榜使用独立 HOT 批次，只接收最终榜；历史回补允许 DAILY、DELAYED 和 HOT 接口，但仍按交易日创建正式 BACKFILL 批次。
5. 为 15 张业务表实现独立加工器，完成日期校验、主数据过滤、单位转换、质量门禁和原子发布。
6. `top_list` 原始响应允许供应方完全重复行；采集层保留原始数据，加工层按日期、股票和原因去除内容完全一致的重复，内容冲突时失败告警。
7. 动态题材成员、概念成员和所有日级加工任务进入同一个全局串行加工入口，防止批量发布形成瞬时数据库 I/O。
8. 管理接口支持 31 个已启用接口的历史回补、修复、采集重试和加工重试，并展示任务、接口耗时、限流等待和成功率。

## 分区结论

继续使用 6 张月度 RANGE 分区表：`stock_daily`、`stock_technical_daily`、`stock_moneyflow_daily`、`market_theme_member_daily`、`etf_daily`、`etf_share_size_daily`。

本阶段只有 `market_theme_member_daily` 属于高增长多对多日事实表，已经按 `trade_date` 月分区。其余新增表的每日规模为指数配置数量、数百个板块、榜单或事件记录，保留普通表配合交易日、代码和历史查询索引；为这些表增加月分区会放大分区维护、外键和执行计划成本，当前没有收益。

## 验收条件

- 最近 7 个自然日内的全部交易日均通过真实 Tushare 请求完成日任务或历史回补。
- 25 张业务表全部非空，每个交易日具备 18 个日级数据集发布记录。
- 动态概念成员和题材成员范围完整展开，没有使用单次接口结果冒充全市场。
- 原始资产逐文件验证 SHA-256、Schema 指纹和行数，31 个接口均可在运维接口查询。
- 故障注入后的加工人工重试成功；终态采集任务人工重试创建新的 REPAIR 批次。
- 实测行数、分区路由、单日分区裁剪和接口成功率写入完成记录。

执行入口：

```bash
CONFIRM_TEST_DATABASE=stock_data_sync make live-full-validation \
  ARGS="--start 2026-07-13 --end 2026-07-19"
```

## 完成记录

2026-07-19 使用 PostgreSQL 18.4 和真实 Tushare Token，从最新结构空库完成 2026-07-13 至 2026-07-19 回归。5 个交易日中，2026-07-17 走每日四阶段任务，之前 4 日通过后台历史回补；每天均有 18 个日级数据集发布，25 张业务表全部非空。

31 个接口共执行 3712 次主流程物理请求，传输成功率 100%，限流等待累计 800796ms。动态范围实际展开 409 个同花顺概念和 5 日共 3112 个东方财富题材成员任务。3701 个原始资产共 426356 行、48846296 字节，SHA-256、Schema 指纹和行数全部复核通过。

重点表实际行数：`concept_board_member` 72015、`market_theme_member_daily` 101019、`stock_hot_rank_daily` 1498、`stock_top_list_daily` 414、`stock_top_inst_daily` 4481、`stock_limit_event_daily` 798、`stock_limit_step_daily` 51、`ths_board_moneyflow_daily` 2360、`market_index` 10445、`market_index_weight` 3627。其余完整行数见机器报告。

6 张分区表均正确写入 `p202607`：股票日线 27619、技术指标 27619、股票资金流 25984、题材成员 101019、ETF 日线 7983、ETF 份额规模 7946。`EXPLAIN` 证明股票日线和题材成员的单日查询只扫描 202607 分区及其索引。其余新增表单日最大约为题材主表 623、板块资金 472、热榜 300、龙虎榜营业部 966 条，不增加分区。

真实数据发现并修复 3 个问题：`top_list` 返回内容完全相同的重复记录，改为原始层保留、清洗层去重且冲突时报错；长业务表名生成的临时表超过 PostgreSQL 63 字符，改为统一限长；题材成员文本含 NUL，统一文本清洗后通过管理接口重试成功。另修复关闭批次精确重放、完整回补接口数量上限和 JSONB COPY 适配。

加工故障注入后通过管理接口第二次执行成功；终态采集任务人工重试创建新的 REPAIR 批次，两个预期阻塞加工任务在验收后通过管理接口取消，数据库无非终态任务。9 条运维查询路径和 31 个接口观测项全部返回成功。

机器可读报告保存在 `dist/live-validation/recent-full-workflows.json`，测试业务数据和原始资产保留用于后续回归。
