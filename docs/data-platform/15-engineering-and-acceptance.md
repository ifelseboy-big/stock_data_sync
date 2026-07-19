<!-- 来源：https://tcnq6fudd3wh.feishu.cn/docx/WoYqdWMeJoqcOtxUVJxccyjenTe；飞书修订版：88 -->

# 13. 工程模块与实施顺序

```text
app/
├── catalog/                  # ApiSpec、DatasetSpec、发布时间和质量规则
├── storage/
│   └── raw_assets.py         # RawAssetStore与LocalRawAssetStore
├── integrations/
│   └── market_data/          # TushareProvider和共享限流
├── modules/
│   ├── acquisition/          # 批次、采集任务、封存和重试
│   ├── processing/           # 依赖解析、队列、处理器和发布
│   ├── partitions/           # 月分区创建与覆盖检查
│   ├── operations/           # 管理查询模型和人工操作
│   ├── stocks/               # 正式业务数据查询
│   └── system/               # 健康、版本和配置摘要
├── scheduler/
│   ├── runtime.py            # 执行器生命周期和线程容量
│   ├── planners.py           # 阶段、批次和加工计划
│   ├── dispatchers.py        # 采集及加工领取
│   ├── recovery.py           # 启动恢复和周期协调
│   └── runner.py             # 唯一生产入口
├── observability/            # 指标、日志上下文和scheduler metrics server
└── cli/                      # 补采、回填、恢复和诊断命令
```

业务模块继续遵循api → service → repository → models单向依赖。catalog只包含声明和纯规则；Provider只返回源记录；RawAssetStore只处理文件；处理器通过明确接口读取资产并写临时表。不得让Vue页面、APScheduler job函数或Tushare DataFrame直接进入业务仓储层。

**依赖补充。**服务端增加PyArrow用于Parquet流式写入、schema读取和批量转换，版本由uv.lock固定；不使用pandas对象作为业务层契约。监控由operations/system模块、运行表聚合和Scheduler本机状态接口完成，不依赖独立指标采集与看板平台。

| 现有能力 | 当前状态 | 需要完成 |
|-|-|-|
| FastAPI、SQLAlchemy、Alembic | 工程骨架已存在 | 31张表迁移、Repository、查询契约和事务实现 |
| TushareProvider与平滑限流 | 基础代码和测试已存在 | 字段注册、错误分类、分页、schema校验和资产封存 |
| APScheduler与单例锁 | 当前只有数据库心跳扫描 | 阶段计划器、两个派发器、批次关闭、恢复和加工锁 |
| 内置监控 | 管理端页面、前端契约和基础健康接口已存在 | operations/system后端聚合、Scheduler状态接口、规则引擎和资源状态 |
| Vue管理端 | 页面和查询契约正在建设 | 接入真实API、追溯详情、告警和受控人工操作 |
| 原生进程部署 | API、Scheduler和Web入口已存在 | 统一运行入口、原生服务配置、raw目录、备份和健康检查 |

**实施顺序。**完整范围按依赖顺序交付，不把未完成的半条链路上线：

1. 数据库迁移：31张表、分区父表、索引、数据库角色和分区管理器。
2. 声明与原始层：ApiSpec、DatasetSpec、LocalRawAssetStore、Parquet封存和哈希校验。
3. 采集链路：阶段计划、批次与任务、共享限流、拆分分页、重试、关闭和启动恢复。
4. 加工链路：依赖解析、全局串行队列、处理器模板、25张业务表转换和dataset_release。
5. 入口与后台：运维查询、补采/回填/重试状态机、业务消费门禁和Vue页面。
6. 可观测与生产化：内置监控聚合、告警规则、系统状态、备份、恢复演练和容量压测。

每一步都必须带Alembic迁移、单元测试、PostgreSQL集成测试和失败路径测试。接口处理器按数据集逐个完成，但只有其采集、加工、发布、监控和恢复全部通过后，才算该数据集交付。

# 14. 验收标准与实施基线

| 验收域 | 必须通过的条件 |
|-|-|
| 数据库 | 31张表、约束、4张月分区表和确认索引与文档一致；未来3个月分区已创建 |
| 采集 | 限流预算全进程共享；分页或拆分达到上限时不会截断；成功任务必有可校验Parquet资产 |
| 批次 | 重复触发不创建重复批次和任务；批次只能关闭一次，关闭后迟到数据进入REPAIR |
| 加工 | 单个任务可解析多个必需资产；任一依赖缺失时不写正式表；全局运行数始终不超过1 |
| 发布 | 正式写入和dataset_release同事务；故障注入后消费者看不到半成品 |
| 重试 | 加工重试不调用Tushare；采集网络重试每次重新申请额度；不可恢复错误不会无限重试 |
| 恢复 | 在采集写文件前后、数据库提交前后和正式表事务中注入进程退出，重启后状态均可自动协调 |
| 数据质量 | 主键、日期、代码、单位换算、跨接口校验和覆盖边界均有自动测试 |
| 性能 | 4并发采集下不超过480次/分钟；运维30日范围查询P95小于500ms；加工无长事务锁住管理查询 |
| 时效 | 正常交易日stock_daily.core在20:30前发布，最终热榜23:10前发布，ETF份额对应业务日在下一可用日12:00前发布 |
| 监控 | 停止任一核心进程、制造Token错误、阻塞加工、删除未来分区和触发存储保护线时产生预期告警 |
| 备份 | 隔离环境可从PostgreSQL备份和Parquet资产恢复，并成功重放至少一个业务日期 |
| 安全 | Token和口令不出现在日志、API和前端；普通研究账号无法写正式表或读取运行敏感字段 |

**上线门禁。**历史回填不能与首个交易日生产链路同时首次运行。先完成主数据初始化和最近5个交易日端到端验证，再启用日常调度；日常链路稳定后，BACKFILL以低优先级分批推进并受队列、磁盘和备份反压控制。

以下项目作为实现基线，不再作为运行环境确认事项：

- [x] 原始数据保留Parquet，使用单机data/raw目录、Zstandard压缩和不可变封存。

- [x] 交易日DAILY批次08:45首次创建，09:25、16:10、17:30、19:00依次复用并追加任务。

- [x] 不新增表；collection_batch、processing_task、processing_dependency补充闭合调度与依赖状态机所需字段和索引。

- [x] 采集默认最多4并发，加工全局并发固定为1，不引入Redis、Kafka、Celery和Airflow。

- [x] 监控由项目内置operations/system模块、管理端页面和规则引擎完成，不引入独立指标采集、告警或看板平台。

- [x] 管理写接口使用单一ADMIN_API_TOKEN和结构化审计日志，不新增多用户账号及审计表。

- [x] 正常交易日发布目标：stock_daily.core 20:30、最终热榜23:10、ETF份额对应业务日在下一可用日12:00。

