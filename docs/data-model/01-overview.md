# 数据范围与全局约定

最终设计包含 **25 张业务数据表**和 **6 张系统运行表**。业务表服务研究、筛选和策略消费；系统运行表服务采集、重试、依赖、原始数据追溯和原子发布，两者用途不同。

| 分类 | 最终表 | 主要用途 |
|-|-|-|
| 基础主数据 | trade_calendar、stock、stock_company、concept_board | 交易日门禁，以及股票和概念的稳定标识 |
| A股行情与资金 | stock_daily、stock_technical_daily、stock_moneyflow_daily、ths_board_moneyflow_daily、stock_suspend_daily | 日线、复权、估值、技术因子、资金和停复牌 |
| 重点专题 | concept_board_daily、concept_board_member、stock_hot_rank_daily、market_theme_daily、market_theme_member_daily、stock_top_list_daily、stock_top_inst_daily、stock_limit_event_daily、stock_limit_step_daily | 概念、热榜、题材、龙虎榜、涨跌停和连板 |
| 指数 | market_index、market_index_daily、index_daily_basic、market_index_weight | 指数主数据、行情、估值和月度成分权重 |
| ETF | etf、etf_daily、etf_share_size_daily | ETF主数据、日线、复权、份额和规模 |
| 系统运行 | collection_batch、collection_task、raw_data_asset、processing_task、processing_dependency、dataset_release | 保证多接口合并数据完整、可重试、可追溯 |

本期不包含实时、分钟、周线、月线和财务报表接口。周/月线可由日线派生；股票名称历史等低频能力暂不进入最终核心表。供应方数据覆盖边界见顶层[Tushare 采集设计](../03-tushare-collection.md)。

## 全局设计约定

| 项目 | 最终口径 |
|-|-|
| 数据库 | PostgreSQL 16；日期使用 date，时间使用 timestamptz，日内时段原文使用 varchar。 |
| 证券代码 | 股票和ETF统一 varchar(16)，指数和板块统一 varchar(20)。不把股票、ETF、指数合并为一张证券表。 |
| 价格/点位 | numeric(20,6)。 |
| 金额 | 统一人民币元 numeric(24,4)。源单位千元乘1000，万元乘10000，亿元乘100000000。 |
| 成交量/份额 | 统一股或份 numeric(24,4)。源单位手乘100，源单位万份乘10000。 |
| 百分比 | numeric(14,6)，保留Tushare百分数口径；3.5表示3.5%，不转换为0.035。 |
| 交易所 | 内部统一 SSE/SZSE/BSE。ETF接口返回的SH/SZ在入库时转换，同时保留source_exchange。 |
| NULL | 主键、业务日期、来源和必要标识不允许NULL；PE/PB、停牌行情、盘后成交、THS暂无字段等按源语义允许NULL，禁止用0代替缺失。 |
| 时间含义 | synced_at只表示正式行最后写入时间，不表示全部接口已经齐备；完整性以dataset_release为准。 |
| 正式表写入 | 原始接口先落raw_data_asset，采集批次关闭后再加工；同一业务日期按事务原子替换或原子发布。 |

正式表的分区和索引按下述物理设计执行：4张大事实表按trade_date月分区，其余表保持普通表；索引只覆盖明确访问路径，不为每个字段机械建索引。所有外键只约束正式表，原始数据资产不设置业务外键，避免供应方异常数据无法留痕。

### 物理分区设计

分区只用于持续增长且补采、重算、清理均以交易日为边界的大事实表。依据[PostgreSQL 16声明式分区规则](https://www.postgresql.org/docs/16/ddl-partitioning.html)，采用trade_date月度RANGE分区；范围左闭右开，主键必须包含trade_date。其他表保持普通表，避免无收益的分区数量、DDL和查询规划成本。

| 表 | 分区策略 | 原因 |
|-|-|-|
| stock_daily | 按trade_date月分区 | 股票日线、估值和复权因子长期累计，预计千万级。 |
| stock_technical_daily | 按trade_date月分区 | 与股票日线同量级，历史复权或指标重建需要按时间隔离。 |
| stock_moneyflow_daily | 按trade_date月分区 | 与股票日线同量级，历史补采和重放按交易日执行。 |
| market_theme_member_daily | 按trade_date月分区 | 保存每日题材成员快照，预计是增长最快的数据集。 |
| 其余21张业务表 | 普通表 | 主数据、当前快照或日增量较小；依靠主键和针对性索引即可。 |
| 6张系统运行表 | 普通表 | 通过运行记录归档控制规模；当前不承担大范围行情扫描。 |

```sql
CREATE TABLE stock_daily (
    -- 字段见4.4节
    ts_code varchar(16) NOT NULL,
    trade_date date NOT NULL,
    ...,
    PRIMARY KEY (ts_code, trade_date)
) PARTITION BY RANGE (trade_date);

CREATE TABLE stock_daily_p202607
PARTITION OF stock_daily
FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
```

四张分区表统一使用“表名_pYYYYMM”命名。系统启动和每日调度前检查分区，至少预建当前月及未来3个月；历史回填先创建覆盖区间的分区。禁止DEFAULT分区，分区缺失必须使加工任务BLOCKED并报警，不能把数据静默写入兜底分区。暂不设置自动删除历史分区，也不做按股票代码的二级分区。

### 索引设计原则

| 规则 | 最终口径 |
|-|-|
| 主键和唯一约束 | 自动生成B-tree索引，不再创建相同前缀、相同顺序的重复索引。 |
| 复合索引顺序 | 按主要查询条件排列左侧列；等值条件在前，排序或范围字段在后。遵循[多列索引左侧列规则](https://www.postgresql.org/docs/16/indexes-multicolumn.html)。 |
| 分区表索引 | 在父表创建，PostgreSQL自动为现有及后续分区建立对应子索引。 |
| 部分索引 | 仅用于待执行、待重试等占比小且查询条件稳定的运行状态；不使用大量部分索引代替分区。 |
| JSONB | 仅stock_hot_rank_daily.concept建立GIN，用于概念标签包含查询；request_params暂不建GIN。 |
| 技术指标 | MACD、RSI、KDJ等不逐字段建索引。单日筛选先通过trade_date缩小到约5500只股票，再扫描指标列。 |
| BRIN | 当前月分区无需BRIN。只有未分区追加表达到较大规模且字段与物理写入顺序高度相关时再评估。 |

#### 分区事实表必建索引

```sql
CREATE INDEX idx_stock_daily_trade_code
    ON stock_daily (trade_date, ts_code);

CREATE INDEX idx_stock_technical_trade_code
    ON stock_technical_daily (trade_date, ts_code);

CREATE INDEX idx_stock_moneyflow_trade_code
    ON stock_moneyflow_daily (trade_date, ts_code);

CREATE INDEX idx_theme_member_stock
    ON market_theme_member_daily (trade_date, ts_code, theme_code);
```

各表主键继续服务“单证券/单题材历史查询”；上述反向索引服务“单日全市场截面”和“某股票当日所属题材”查询。四个索引均在分区父表创建。

#### 概念、热榜、题材与龙虎榜索引

```sql
CREATE INDEX idx_concept_daily_trade_board
    ON concept_board_daily (trade_date, source, ts_code);

CREATE INDEX idx_concept_member_stock
    ON concept_board_member (con_code, source, ts_code);

CREATE INDEX idx_hot_rank_stock_history
    ON stock_hot_rank_daily (ts_code, trade_date, source, rank_type);

CREATE INDEX idx_hot_rank_concept_gin
    ON stock_hot_rank_daily USING gin (concept);

CREATE INDEX idx_theme_daily_trade_rank
    ON market_theme_daily (trade_date, source, rank);

CREATE INDEX idx_top_list_stock_history
    ON stock_top_list_daily (ts_code, trade_date);

CREATE INDEX idx_top_inst_trade_stock
    ON stock_top_inst_daily (trade_date, ts_code);

CREATE INDEX idx_top_inst_stock_history
    ON stock_top_inst_daily (ts_code, trade_date);

CREATE INDEX idx_top_inst_exalter_history
    ON stock_top_inst_daily (exalter, trade_date);

CREATE INDEX idx_limit_event_stock_history
    ON stock_limit_event_daily (ts_code, trade_date, limit_type);

CREATE INDEX idx_limit_event_day_type
    ON stock_limit_event_daily (trade_date, limit_type, ts_code);

CREATE INDEX idx_limit_step_day_nums
    ON stock_limit_step_daily (trade_date, nums DESC, ts_code);

CREATE INDEX idx_suspend_day_type
    ON stock_suspend_daily (trade_date, suspend_type, ts_code);

CREATE INDEX idx_ths_board_flow_day_amount
    ON ths_board_moneyflow_daily (trade_date, board_type, net_amount DESC);
```

stock_hot_rank_daily已有按日期和排名的唯一索引，stock_top_list_daily已有以trade_date开头的唯一索引，因此不重复创建同方向索引。GIN索引按PostgreSQL的[JSONB索引规则](https://www.postgresql.org/docs/16/datatype-json.html#JSON-INDEXING)支持concept标签存在和包含查询。

#### 股票、指数与 ETF 索引

```sql
CREATE UNIQUE INDEX uq_stock_exchange_symbol
    ON stock (exchange, symbol);

CREATE INDEX idx_market_index_daily_trade_code
    ON market_index_daily (trade_date, ts_code);

CREATE INDEX idx_index_weight_member
    ON market_index_weight (con_code, snapshot_date, index_code);

CREATE INDEX idx_etf_daily_trade_code
    ON etf_daily (trade_date, ts_code);

CREATE INDEX idx_etf_share_trade_code
    ON etf_share_size_daily (trade_date, ts_code);
```

trade_calendar、stock_company、concept_board、market_index、index_daily_basic和etf的数据量较小，现有主键已覆盖主要访问路径，不额外建低收益索引。

#### 系统运行表索引

```sql
CREATE UNIQUE INDEX uq_collection_batch_slot
    ON collection_batch (batch_type, business_date, scheduled_at)
    NULLS NOT DISTINCT;

CREATE INDEX idx_batch_active_schedule
    ON collection_batch (scheduled_at, batch_id)
    WHERE status IN ('PENDING', 'RUNNING');

CREATE INDEX idx_task_batch_status
    ON collection_task (batch_id, status);

CREATE INDEX idx_task_retry_due
    ON collection_task (next_retry_at, task_id)
    WHERE status = 'RETRY_WAIT';

CREATE INDEX idx_raw_asset_api_date
    ON raw_data_asset (api_name, business_date, fetched_at);

CREATE UNIQUE INDEX uq_processing_output_version
    ON processing_task (output_version);

CREATE INDEX idx_process_batch_status
    ON processing_task (source_batch_id, status);

CREATE INDEX idx_process_queue
    ON processing_task (priority, queued_at, process_id)
    WHERE status = 'QUEUED';

CREATE INDEX idx_processing_retry_due
    ON processing_task (next_retry_at, priority, process_id)
    WHERE status = 'RETRY_WAIT';

CREATE INDEX idx_dependency_asset
    ON processing_dependency (resolved_asset_id)
    WHERE resolved_asset_id IS NOT NULL;

CREATE INDEX idx_dependency_release_process
    ON processing_dependency (resolved_release_process_id)
    WHERE resolved_release_process_id IS NOT NULL;

CREATE INDEX idx_dependency_waiting
    ON processing_dependency (process_id, dependency_type, status);

CREATE INDEX idx_release_process
    ON dataset_release (process_id);

CREATE INDEX idx_release_business_date
    ON dataset_release (dataset_name, business_date)
    WHERE business_date IS NOT NULL;
```

运行表部分索引只覆盖少量活动状态，减少索引体积和状态更新开销。processing_task约定priority数值越小优先级越高，队列SQL固定使用ORDER BY priority, queued_at, process_id。所有部分索引的查询条件必须与调度SQL保持一致，否则PostgreSQL无法使用对应索引。

### 分区与索引运维验收

| 环节 | 验收规则 |
|-|-|
| DDL初始化 | 创建所有历史回填月份、当前月和未来3个月分区；确认不存在DEFAULT分区。 |
| 月度维护 | 每月25日再次确保未来3个月分区已存在；操作必须幂等。 |
| 批量写入 | 历史回填或大批量重放完成后对受影响分区执行ANALYZE。 |
| 查询计划 | 使用EXPLAIN (ANALYZE, BUFFERS)确认trade_date条件触发分区裁剪，实际只访问目标月份。 |
| 索引审计 | 通过pg_stat_user_indexes和慢查询记录审查索引；不得仅凭短期idx_scan=0删除主键、唯一约束或外键支撑索引。 |
| 新增索引 | 必须对应已存在的慢查询或稳定查询模式，并在同等数据量下验证写入开销和查询收益。 |

本设计不承诺“分区一定让所有查询更快”。分区的主要价值是日期裁剪、历史补采和批量维护；索引负责具体访问路径。两者必须通过真实SQL执行计划验收。
