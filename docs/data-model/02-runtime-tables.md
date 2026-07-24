# 系统运行与数据发布表

本文件描述采集、加工、发布和调度管理运行表。它们不是新增行情业务概念，而是实现“先采集、封存版本、检查依赖、再发布”和可追溯调度的控制面模型。

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
processing_plan_version varchar(64) null -- 最近完整解析过的 DatasetSpec 目录指纹
processing_planned_at timestamptz null -- 最近一次完整生成加工计划的时间
started_at timestamptz null -- 批次实际开始时间
closed_at timestamptz null -- 批次关闭时间
created_at timestamptz not null -- 批次记录创建时间
```

一组相同业务周期的采集任务。最终阶段必须在同一事务中写完全部任务并填写plan_version、expected_task_count和planning_completed_at；只有实际任务数与预期相等且全部终态后才能关闭。批次关闭后不可重新打开，补采必须创建新的REPAIR批次。加工规划器只领取 `processing_plan_version` 与当前目录指纹不同的关闭批次，每批次独立提交，完整解析且不存在等待活动上游发布的依赖后才更新水位；升级前的历史关闭批次保留空水位并由限量队列重新验证。`idx_collection_batch_processing_plan (processing_plan_version ASC NULLS FIRST, closed_at DESC NULLS LAST, batch_id) WHERE status = 'CLOSED'` 服务增量领取，空水位和最近关闭批次优先，避免新任务被历史积压阻塞。

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
execution_token uuid null -- 当前 RUNNING 尝试的唯一执行租约；离开 RUNNING 时清空
request_count integer not null default 0 -- 实际接口请求次数，包含分页请求
row_count integer null -- 接口返回并合并后的总行数
started_at timestamptz null -- 本次任务开始时间
finished_at timestamptz null -- 任务最终结束时间
error_code varchar(64) null -- 最近一次错误码
error_message text null -- 最近一次错误说明
warning_message text null -- 采集结果有效但存在历史范围缺口等可接受数据质量问题
unique (batch_id, api_name, scope_key) -- 同一批次内接口和调用范围唯一
```

一个任务只对应一个接口和一个确定范围。scope_key用于表达交易日、股票批次、指数代码或概念代码，避免重复调度。每次领取都生成新的 `execution_token`；完成、失败和恢复操作必须在行锁内同时匹配 `RUNNING` 与该 token，旧 worker 在超时回收后返回时只能得到当前状态，不能登记资产或覆盖新执行。原始资产路径包含 execution token，避免两个执行代次写入同一个最终文件。`warning_message` 不改变成功状态，数据缺口告警可据此展示并继续保留发布资格。普通运行记录分页不计算恢复状态，只执行基础合并、计数、排序和分页；只有“未恢复失败任务”筛选需要判断失败任务是否已被同 `provider + api_name + scope_key` 的后续成功或活动任务恢复。接口计划变化时必须重新生成当前范围任务并覆盖，不能用业务日等模糊条件兼容旧任务身份。`idx_collection_task_recovery (api_name, scope_key, finished_at) WHERE status IN ('SUCCESS','EMPTY_VALID')` 为恢复筛选提供直接访问路径。

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
execution_token uuid null -- 当前 RUNNING 尝试的唯一执行租约；离开 RUNNING 时清空
queued_at timestamptz null -- 依赖就绪并进入加工队列的时间
started_at timestamptz null -- 加工开始时间
finished_at timestamptz null -- 加工结束时间
rows_read integer null -- 从原始资产读取的行数
rows_rejected integer null -- 因校验失败被拒绝的行数
rows_written integer null -- 写入目标版本的行数
error_message text null -- 加工失败或阻塞的错误说明
warning_message text null -- 加工成功但存在可接受数据质量问题时的警告说明
```

消费一个或多个原始资产并生成正式数据。每次领取都生成新的 `execution_token`，正式表写入、发布切换和失败回写前必须在行锁内确认 token 仍属于当前 `RUNNING`；超时恢复会清空旧 token，防止旧 worker 在新尝试开始后重复发布或覆盖状态。`process_type` 保存处理器名称和版本；规划器发现同批次、同输出数据集存在旧版本活动任务时，取消尚未运行的旧任务并创建当前版本，旧版本运行任务不抢占但当前版本任务仍会生成，已经成功的历史任务不因处理器升级自动重算。`warning_message` 与 `error_message` 分离：警告不会把成功任务改为失败，也不会阻止数据发布，但会进入运维查询供人工复核。失败是否已经恢复，优先按 `dataset_release` 主键 `(dataset_name, scope_type, scope_key)` 精确查找当前发布；尚未发布时再按同数据集、同发布范围查找活动加工任务。`idx_processing_active_recovery (output_dataset, business_date) INCLUDE (source_batch_id, queued_at, started_at)` 仅覆盖活动状态，为后一个判断提供访问路径。加工入口并发上限由 `PROCESSING_MAX_WORKERS` 控制，默认 3；DATE 数据集同一输出数据集的不同业务日期可以并行，其他发布范围仍按输出数据集互斥。

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

## deferred_collection_stage

```sql
stage_id uuid primary key -- 延迟采集阶段唯一标识
command_id uuid not null references operation_command(command_id) on delete cascade -- 来源人工命令
api_name varchar(64) not null -- 等待动态范围就绪后需要创建任务的接口
business_date date not null -- 阶段对应的业务日期
batch_type varchar(20) not null check (batch_type in ('BACKFILL','REPAIR')) -- 历史回填或修复
status varchar(16) not null default 'PENDING' check (status in ('PENDING','PLANNED')) -- 等待计划或已完成计划
batch_id uuid null references collection_batch(batch_id) on delete set null -- 动态阶段最终生成的批次
planned_at timestamptz null -- 阶段完成计划的时间
created_at timestamptz not null default current_timestamp -- 阶段创建时间
unique (command_id, api_name, business_date) -- 同一命令、接口和日期只保存一个阶段
```

历史回填或修复中的动态接口不能在命令创建时一次性展开。例如 `ths_member` 需要先发布同花顺概念和主题主数据，再按最新板块代码生成采集范围。命令创建时先持久化 `PENDING` 阶段；Scheduler 周期扫描依赖，满足后创建后续批次并原子更新为 `PLANNED`。因此程序升级、进程重启或主机恢复不会丢失尚未展开的阶段。`idx_deferred_collection_stage_pending (created_at, stage_id) WHERE status = 'PENDING'` 用于稳定领取待计划记录。

## scheduled_job_control

```sql
job_id varchar(96) primary key -- 代码目录中的稳定调度任务 ID
enabled boolean not null default true -- 是否接受定时触发和启动补偿
updated_at timestamptz not null -- 最近修改时间
updated_by varchar(64) null -- 最近操作人
```

只保存运维控制状态，Cron/Interval 定义仍由代码目录统一声明，防止数据库配置与可执行函数漂移。停用不删除 APScheduler 任务，执行包装器在触发时跳过；人工执行仍需单独鉴权和审计。

## scheduled_job_execution

```sql
execution_id uuid primary key
job_id varchar(96) not null
trigger_type varchar(24) not null check (trigger_type in ('SCHEDULED','MANUAL','STARTUP_CATCHUP'))
status varchar(16) not null check (status in ('PENDING','RUNNING','SUCCESS','FAILED'))
requested_by varchar(64) null
reason varchar(500) null
scheduled_at timestamptz null
started_at timestamptz null
finished_at timestamptz null
duration_ms integer null
error_message varchar(2000) null
created_at timestamptz not null
unique (job_id) where status = 'RUNNING'
unique (job_id) where status = 'PENDING'
```

记录可管理调度任务的实际执行结果。人工命令先写入 `PENDING`，由 Scheduler 领取并执行；定时和启动补偿直接创建 `RUNNING` 记录，结束后写入耗时与失败原因。同一 `job_id` 的定时、人工和启动补偿共用会话级 advisory lock，数据库唯一部分索引再保证最多一条 `RUNNING` 和一条 `PENDING`。Scheduler 获得单例锁后先完整锁定并把上次进程遗留的 `RUNNING` 收口为 `FAILED`，保留 `PENDING` 等待后续派发。运行时人工派发按顺序尝试全部 `PENDING`，真实忙 Job 因 advisory lock 获取失败而被跳过；一旦取得 Job 锁，先收口该 Job 遗留的 `RUNNING` 再执行新请求，使结果回写失败也能在下一轮自愈。`job_id, created_at` 索引服务最近结果和历史分页，`PENDING` 部分索引服务人工执行队列。终态执行记录默认保留 30 天，由每日维护任务清理；保留天数可配置，`PENDING` 和 `RUNNING` 记录不参与清理。
