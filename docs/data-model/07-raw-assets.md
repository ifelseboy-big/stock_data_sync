# 原始数据与 Parquet 资产设计

**Parquet需要保留。**它只承担“原始资产层”，不是第二套正式数据库。原始资产定义为Tushare SDK完成分页或分片合并后的返回结果，在任何字段改名、单位换算、跨接口关联和业务过滤之前封存。正式消费者不直接读取Parquet。

| 项目 | 规则 |
|-|-|
| 文件格式 | Apache Parquet，Zstandard压缩；一个collection_task对应一个文件 |
| 字段 | 保持Tushare源字段名、字段顺序、返回值和NULL；不执行元、股、份或百分比换算 |
| 分页 | 分页结果使用Arrow批次流式写入同一个临时Parquet；全部分页完成并证明连续、无截断后才封存为一个资产，不在内存中累积全部结果 |
| 空结果 | 合法空结果写入带预期schema的零行Parquet，并将任务记为EMPTY_VALID |
| 压缩与行组 | compression=zstd，row_group_size默认100000；小文件仍保持单文件 |
| 完整性 | 文件关闭后计算SHA-256写入content_hash，字段名、顺序和Arrow类型生成schema_fingerprint |
| 可变性 | sealed_at写入后文件不可覆盖；补采或修订必须产生新任务和新资产 |

供应方兼容仍遵守原始层不清洗原则：`dc_hot` 即使在 `is_new=Y` 下返回多个 `rank_time`，所有快照都写入同一任务资产，由加工层选择最新完整快照；`moneyflow_cnt_ths.ts_code` 为空时按原值保存 `NULL`，不在 Parquet 中补码。只有正式加工层可以基于接口业务规则选择快照或业务键。

```text
data/raw/
└── tushare/
    └── {api_name}/
        └── business_date={YYYY-MM-DD或_GLOBAL}/
            └── batch_id={batch_uuid}/
                └── task_id={task_uuid}/asset.parquet
```

**原子封存。**采集器在最终目录同一文件系统内写临时文件，完成flush、文件fsync、行数/schema/哈希检查后原子rename，再fsync父目录，随后在短事务中插入raw_data_asset并更新collection_task。最终路径由task_id确定。reconciler按“临时文件、最终文件、资产记录、任务状态”四项组合恢复：四项一致则确认成功；只有最终文件时验证后补登记或隔离；只有临时文件时清理并重试；资产记录存在但文件缺失时标记失败并告警。孤儿状态由文件与数据库扫描重建，不依赖内存清单。

**存储抽象。**代码通过RawAssetStore接口访问资产，首期实现LocalRawAssetStore并在storage_uri保存file URI。以后迁移S3兼容存储时只增加S3RawAssetStore，不修改采集和加工逻辑。首期不同时保存JSONL副本，避免原始数据双份存储和一致性问题。

**保留和反压。**已封存资产默认不自动删除，因为规则重算、问题追溯和正式表恢复依赖它们。容量门禁同时检查使用率和配置的绝对保留空间：达到预警线停止新BACKFILL，达到保护线暂停非紧急采集，始终优先保证PostgreSQL、WAL和临时文件可写。删除资产必须先确认存在可恢复副本，并作为独立受审计操作执行。
