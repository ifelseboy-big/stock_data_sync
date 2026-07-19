<!-- 来源：https://tcnq6fudd3wh.feishu.cn/docx/WoYqdWMeJoqcOtxUVJxccyjenTe；飞书修订版：88 -->

# 4. 数据库落地设计

数据库沿用确认稿的31张表；25张业务表字段不变，系统运行表仅补充闭合状态机所必需的字段：collection_batch增加计划冻结信息，processing_task增加自动重试信息，processing_dependency增加多资产及数据集发布依赖信息。所有表继续使用默认schema，避免ORM、Alembic和跨schema外键复杂化。

| 数据库对象 | 落地规则 |
|-|-|
| 25张业务表 | 字段、类型、主外键、单位和NULL口径严格使用确认稿 |
| 6张系统运行表 | 作为批次、任务、资产、依赖和发布的唯一事实来源 |
| 4张分区事实表 | stock_daily、stock_technical_daily、stock_moneyflow_daily、market_theme_member_daily按trade_date月分区 |
| 其余表 | 普通表；仅建立确认稿列出的访问路径索引 |
| APScheduler JobStore | 使用独立apscheduler_jobs表，只保存系统触发器，不保存业务任务结果 |

**collection_batch必要字段修正。**批次必须证明最终计划已经完整生成，不能只根据“当前任务都已终态”关闭。最终阶段在同一事务内写完全部任务后冻结计划：

```sql
ALTER TABLE collection_batch
    ADD COLUMN plan_version varchar(64) NULL,
    ADD COLUMN expected_task_count integer NULL,
    ADD COLUMN planning_completed_at timestamptz NULL;
```

**processing_task必要字段修正。**该表已有RETRY_WAIT状态，但确认稿缺少自动重试所需的次数和到期时间。必须补充以下3个字段，语义与collection_task一致；否则只能人工重试，无法满足已确认的加工重试流程。

```sql
ALTER TABLE processing_task
    ADD COLUMN attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN max_attempts integer NOT NULL DEFAULT 3,
    ADD COLUMN next_retry_at timestamptz NULL;
```

**processing_dependency必要字段修正。**同一加工任务可能依赖同一接口的多个分片，也可能依赖另一数据集已发布的范围。仅靠dependency_name和resolved_asset_id无法表达这两种情况，必须补充依赖类型、稳定范围键和已解析发布任务，并调整主键。

```sql
ALTER TABLE processing_dependency
    ADD COLUMN dependency_type varchar(20) NOT NULL,
    ADD COLUMN dependency_scope_key varchar(256) NOT NULL,
    ADD COLUMN resolved_release_process_id uuid NULL REFERENCES processing_task(process_id);

ALTER TABLE processing_dependency
    DROP CONSTRAINT processing_dependency_pkey,
    ADD PRIMARY KEY (process_id, dependency_type, dependency_name, dependency_scope_key),
    ADD CONSTRAINT ck_processing_dependency_target CHECK (
        (dependency_type = 'RAW_ASSET' AND resolved_release_process_id IS NULL)
        OR (dependency_type = 'DATASET_RELEASE' AND resolved_asset_id IS NULL)
    );
```

**必须补充的运行索引。**调度器需要数据库级批次幂等、输出版本幂等、到期重试和依赖解析索引。批次时隙唯一，output_version全局唯一，等待依赖可按类型和状态检索。

```sql
CREATE UNIQUE INDEX uq_collection_batch_slot
ON collection_batch (batch_type, business_date, scheduled_at)
NULLS NOT DISTINCT;

CREATE UNIQUE INDEX uq_processing_output_version
ON processing_task (output_version);

CREATE INDEX idx_processing_retry_due
ON processing_task (next_retry_at, priority, process_id)
WHERE status = 'RETRY_WAIT';

CREATE INDEX idx_dependency_waiting
ON processing_dependency (process_id, dependency_type, status);

CREATE INDEX idx_dependency_release_process
ON processing_dependency (resolved_release_process_id)
WHERE resolved_release_process_id IS NOT NULL;
```

批次创建使用固定计划时间，不使用实际执行时间。例如16:10阶段即使服务17:00恢复，scheduled_at仍沿用当日DAILY批次的08:45计划时隙，从而命中原批次而不是生成重复批次。output_version由source_batch_id、output_dataset、process_type中的处理器版本和business_date计算确定性UUID；同一批次计划不会重复生成加工任务，规则升级或新的REPAIR批次会得到新版本。

**事务边界。**任务领取只在短事务内完成状态切换，接口调用和Parquet读写不得占用数据库事务。正式表写入、数据校验结果确认和dataset_release切换位于同一数据库事务；失败时整体回滚。大批量写入先进入会话级临时表，再使用COPY和集合SQL写入目标表。

**分区维护。**启动时和每日08:30检查四张分区表，至少预建当前月及未来3个月；历史回填先按请求区间建分区。分区缺失使对应加工任务进入BLOCKED并告警，禁止DEFAULT分区。大批量回填完成后对受影响分区执行ANALYZE；清理或重写后依据膨胀情况执行VACUUM，不在高峰期自动VACUUM FULL。

**连接与权限。**迁移账号负责DDL，应用账号只具有DML和序列权限，查询消费者使用只读账号。API和Scheduler分别配置连接池，连接总数必须小于PostgreSQL max_connections的70%，为迁移、备份、内置状态查询和人工诊断保留余量。

