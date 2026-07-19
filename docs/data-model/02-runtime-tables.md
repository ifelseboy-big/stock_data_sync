# 系统运行与数据发布表

这6张表不是新增行情业务概念，而是实现“先采集、封存版本、检查依赖、再发布”的最小运行模型。缺少它们，stock_daily等多接口合并表无法判断是否为半成品。

## collection_batch

```sql
batch_id uuid primary key -- 采集批次唯一标识
batch_type varchar(20) not null check (batch_type in ('MASTER','DAILY','HOT','DELAYED','BACKFILL','REPAIR')) -- 批次类型：主数据、日常、热榜、延迟数据、历史回填或修复
business_date date null -- 批次对应的业务日期；无明确业务日期时为空
status varchar(20) not null check (status in ('PENDING','RUNNING','CLOSED','CANCELLED')) -- 批次生命周期状态
scheduled_at timestamptz not null -- 计划触发时间
plan_version varchar(64) null -- 最终任务计划的代码版本；计划冻结前为空
expected_task_count integer null -- 计划冻结后的预期任务总数
planning_completed_at timestamptz null -- 最终阶段全部任务生成并冻结计划的时间
started_at timestamptz null -- 批次实际开始时间
closed_at timestamptz null -- 批次关闭时间
created_at timestamptz not null -- 批次记录创建时间
```

一组相同业务周期的采集任务。最终阶段必须在同一事务中写完全部任务并填写plan_version、expected_task_count和planning_completed_at；只有实际任务数与预期相等且全部终态后才能关闭。批次关闭后不可重新打开，补采必须创建新的REPAIR批次。

## collection_task

```sql
task_id uuid primary key -- 采集任务唯一标识
batch_id uuid not null references collection_batch(batch_id) -- 所属采集批次
provider varchar(16) not null default 'TUSHARE' -- 数据供应商标识
api_name varchar(64) not null -- Tushare接口名称
scope_key varchar(256) not null -- 调用范围幂等键，如交易日、代码或代码批次
request_params jsonb not null -- 本次接口调用的完整请求参数
status varchar(20) not null check (status in ('PENDING','RUNNING','SUCCESS','EMPTY_VALID','RETRY_WAIT','FAILED','SKIPPED','CANCELLED')) -- 任务执行状态
attempt_count integer not null default 0 -- 已执行的尝试次数
max_attempts integer not null -- 允许的最大尝试次数
next_retry_at timestamptz null -- 下次允许重试的时间
request_count integer not null default 0 -- 实际接口请求次数，包含分页请求
row_count integer null -- 接口返回并合并后的总行数
started_at timestamptz null -- 本次任务开始时间
finished_at timestamptz null -- 任务最终结束时间
error_code varchar(64) null -- 最近一次错误码
error_message text null -- 最近一次错误说明
unique (batch_id, api_name, scope_key) -- 同一批次内接口和调用范围唯一
```

一个任务只对应一个接口和一个确定范围。scope_key用于表达交易日、股票批次、指数代码或概念代码，避免重复调度。

## raw_data_asset

```sql
asset_id uuid primary key -- 原始数据资产唯一标识
task_id uuid not null unique references collection_task(task_id) -- 产生该资产的采集任务；每个任务只封存一个资产
provider varchar(16) not null -- 数据供应商标识
api_name varchar(64) not null -- 原始数据对应的接口名称
business_date date null -- 原始数据对应的业务日期
request_params jsonb not null -- 生成资产时的请求参数快照
storage_uri text not null -- 封存原始文件的对象存储地址
content_hash char(64) not null -- 原始文件内容的SHA-256哈希
schema_fingerprint char(64) not null -- 返回字段集合及顺序的结构指纹
row_count integer not null -- 封存资产的数据行数
is_complete boolean not null -- 是否完成全部分页并通过完整性检查
fetched_at timestamptz not null -- 原始数据获取完成时间
sealed_at timestamptz not null -- 资产封存并转为不可变状态的时间
```

保存已经合并分页并封存的原始结果。接口字段变化通过schema_fingerprint发现；加工重试只能读取该资产，不重新调用Tushare。

## processing_task

```sql
process_id uuid primary key -- 加工任务唯一标识
source_batch_id uuid not null references collection_batch(batch_id) -- 来源采集批次
process_type varchar(64) not null -- 加工类型或转换规则标识
business_date date null -- 加工结果对应的业务日期
output_dataset varchar(64) not null -- 目标数据集或正式表名称
output_version uuid not null -- 本次加工生成的输出版本
status varchar(20) not null check (status in ('WAITING_DEPENDENCY','QUEUED','RUNNING','RETRY_WAIT','SUCCESS','BLOCKED','FAILED','SKIPPED','CANCELLED')) -- 加工任务状态
priority smallint not null -- 调度优先级；数值越小越优先
attempt_count integer not null default 0 -- 已执行的尝试次数
max_attempts integer not null default 3 -- 允许的最大尝试次数，可由DatasetSpec覆盖
next_retry_at timestamptz null -- 下次允许重试的时间
queued_at timestamptz null -- 依赖就绪并进入加工队列的时间
started_at timestamptz null -- 加工开始时间
finished_at timestamptz null -- 加工结束时间
rows_read integer null -- 从原始资产读取的行数
rows_rejected integer null -- 因校验失败被拒绝的行数
rows_written integer null -- 写入目标版本的行数
error_message text null -- 加工失败或阻塞的错误说明
```

消费一个或多个原始资产并生成正式数据。全局加工入口并发数固定为1，但采集任务可以在Tushare额度内并行。

## processing_dependency

```sql
process_id uuid not null references processing_task(process_id) -- 所属加工任务
dependency_type varchar(20) not null check (dependency_type in ('RAW_ASSET','DATASET_RELEASE')) -- 原始资产依赖或已发布数据集依赖
dependency_name varchar(64) not null -- 接口名或数据集名
dependency_scope_key varchar(256) not null -- 依赖范围的稳定幂等键
dependency_scope jsonb not null -- 日期、代码、分片或发布范围等完整条件
resolved_asset_id uuid null references raw_data_asset(asset_id) -- RAW_ASSET依赖解析出的封存资产
resolved_release_process_id uuid null references processing_task(process_id) -- DATASET_RELEASE依赖解析出的发布加工任务
status varchar(20) not null check (status in ('WAITING','READY','MISSING','FAILED')) -- 依赖解析状态
blocked_reason text null -- 依赖缺失或失败导致阻塞的原因
primary key (process_id, dependency_type, dependency_name, dependency_scope_key) -- 同名依赖可按不同范围保存多条
check ((dependency_type = 'RAW_ASSET' and resolved_release_process_id is null) or (dependency_type = 'DATASET_RELEASE' and resolved_asset_id is null)) -- 两类解析目标互斥
```

所有声明依赖都是必需依赖。RAW_ASSET允许同一接口按多个scope保存多条资产依赖；DATASET_RELEASE记录满足依赖的发布加工任务。只有全部依赖READY后，加工任务才能进入队列。

## dataset_release

```sql
dataset_name varchar(64) not null -- 对消费者发布的数据集名称
scope_type varchar(16) not null check (scope_type in ('GLOBAL','DATE','MONTH','ENTITY')) -- 发布粒度：全局、日期、月份或实体
scope_key varchar(256) not null -- 发布范围唯一键，如GLOBAL、日期或实体代码
business_date date null -- 发布版本对应的明确业务日期
version_id uuid not null -- 当前对消费者可见的数据版本标识
process_id uuid not null references processing_task(process_id) -- 生成该版本的加工任务
row_count integer not null -- 发布版本的数据行数
published_at timestamptz not null -- 版本原子发布完成时间
primary key (dataset_name, scope_type, scope_key) -- 每个数据集和发布范围只保留一个当前版本
```

保存每个数据集范围的完成性、血缘和当前加工结果。主数据使用GLOBAL/GLOBAL，日事实使用DATE/YYYY-MM-DD，指数权重使用MONTH/指数代码:YYYY-MM；按实体发布时使用ENTITY/实体代码。business_date只在存在明确业务日期时填写。正式表写入与release更新在同一事务提交。业务表不保存历史version_id，因此旧版本只用于追溯，不能通过切换指针即时回滚；恢复历史结果必须重新加工并发布新的版本。
