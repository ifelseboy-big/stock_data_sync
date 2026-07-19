<!-- 来源：https://tcnq6fudd3wh.feishu.cn/docx/WoYqdWMeJoqcOtxUVJxccyjenTe；飞书修订版：88 -->

# 8. 数据加工与原子发布

采集批次关闭后，processing_planner根据DatasetSpec生成加工任务。DatasetSpec声明输出数据集、发布范围、处理器版本、必需原始接口、主数据依赖、写入策略和质量规则。所有依赖均为必需依赖，不提供“缺一个接口也先写”的降级路径。

**依赖展开。**一个接口拆成多个采集任务时，每份资产对应一条processing_dependency。dependency_type固定为RAW_ASSET，dependency_name保存api_name，dependency_scope_key保存scope_key或其稳定哈希，dependency_scope保存完整范围；因此同一加工任务可同时依赖daily多个分片、多个ths_member板块或多个dc_concept_cons题材资产。

依赖正式数据集时，dependency_type为DATASET_RELEASE，dependency_scope明确dataset_name、scope_type和scope_key；解析成功后resolved_release_process_id记录满足依赖的发布加工任务，resolved_asset_id保持NULL。RAW_ASSET依赖则只填写resolved_asset_id。例如stock_daily.limit依赖同日stock_daily.core，题材成员依赖同日market_theme_daily。两类依赖都必须持久化解析结果，不能只在运行时查询后丢失。

```text
WAITING_DEPENDENCY
    │ 全部依赖READY
    ▼
QUEUED ──领取唯一加工槽位──> RUNNING
    │                           │
    │                           ├──读取并校验Parquet
    │                           ├──类型解析、单位换算、关联与去重
    │                           ├──写入临时表并执行质量校验
    │                           └──事务写正式表并切换dataset_release
    │
    └──依赖缺失或失败──> BLOCKED

RUNNING ──成功──> SUCCESS
        ├──可恢复──> RETRY_WAIT
        └──不可恢复──> FAILED
```

| 写入策略 | 适用数据 | 事务动作 |
|-|-|-|
| UPSERT_KEY | 有稳定自然键的日行情、指标、资金和指数数据 | 临时表校验后按主键insert on conflict update |
| REPLACE_DATE | 热榜、龙虎榜、题材成员等可能整体修订的数据 | 删除指定业务日期范围后完整插入 |
| REPLACE_ENTITY | THS概念成员当前快照、指数月度成分 | 按板块或指数快照范围完整替换 |
| MASTER_MERGE | stock、etf、market_index、company和concept_board | 全状态集合校验后合并；不因单次缺行直接物理删除 |
| PATCH_COLUMNS | stock_daily的up_limit、down_limit补充 | 只更新已存在核心行的两个字段并单独发布完成状态 |

**stock_daily发布。**daily、daily_basic和adj_factor全部READY后生成stock_daily.core发布；stk_limit只产生stock_daily.limit发布并更新up_limit、down_limit。消费者如必须使用涨跌停价，应同时检查两个release范围；只需要行情和估值时可消费core。

**发布范围与语义。**主数据使用GLOBAL/GLOBAL；日事实使用DATE/YYYY-MM-DD；指数权重使用MONTH/index_code:YYYY-MM；按板块完整替换使用ENTITY/source:board_code。dataset_release是完成性、血缘和当前加工结果登记，正式写入与release更新在同一PostgreSQL事务中原子提交。业务表采用原地更新且不保存version_id，因此旧version_id只用于追溯，不能通过切换指针即时回滚；需要恢复历史结果时，必须从对应原始资产重新加工并发布新的output_version。

**质量门禁。**加工前验证资产哈希和schema_fingerprint；转换后验证主键唯一、外键代码存在、业务日期一致、行数变化、金额和成交量换算、daily与daily_basic收盘价、daily与stk_limit前收盘价、热榜rank唯一及最终时间。任何阻断级校验失败都回滚正式写入。

**优先级与重试。**当期日常加工为100，自动修复为200，普通人工重跑为300，历史回填和规则重算为400；紧急人工任务可设50。相同优先级按business_date和queued_at排序。加工任务每次领取时增加attempt_count；可恢复失败按指数退避并带随机抖动写入next_retry_at，达到max_attempts后转FAILED并告警。加工重试只读取原资产，不再调用Tushare。

**技术指标边界。**stock_technical_daily继续保存Tushare stk_factor快照。未来本地动态前复权、MACD或其他指标必须建立新的DatasetSpec、处理器版本和输出数据集，依赖完整stock_daily历史，不能覆盖现有表的源快照语义。

