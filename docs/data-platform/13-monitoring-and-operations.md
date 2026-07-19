<!-- 来源：https://tcnq6fudd3wh.feishu.cn/docx/WoYqdWMeJoqcOtxUVJxccyjenTe；飞书修订版：88 -->

# 10. 监控、告警与运维看板

监控由项目自身实现，不部署独立指标采集、告警或看板平台。后端operations与system模块聚合运行表、PostgreSQL状态、本机资源和Scheduler状态，前端直接使用现有运行概览、任务队列、接口监控、运行记录和告警中心页面。结构化日志负责单次执行追踪。

| 监控入口 | 数据来源 | 主要内容 |
|-|-|-|
| /operations/overview | collection_batch、collection_task、processing_task及Scheduler状态 | 运行中批次、加工槽位、阻塞任务、今日成功率、最近异常 |
| /operations/acquisition-batches | collection_batch、collection_task、raw_data_asset | 批次进度、接口任务、重试、空结果和资产封存状态 |
| /operations/processing-queue | processing_task、processing_dependency | 队列顺序、当前运行任务、依赖就绪数和阻塞原因 |
| /operations/providers/tushare | collection_task聚合与Scheduler限流器快照 | 接口调用量、成功率、P50/P95、重试、空结果和当前额度 |
| /operations/releases | dataset_release与业务质量查询 | 最新发布业务日、发布延迟、行数变化和缺失范围 |
| /system/resources | PostgreSQL系统视图和本机资源采样 | 连接、锁、长事务、数据库大小、CPU、内存、磁盘和目录容量 |
| /operations/alerts | 内置规则引擎实时计算 | 任务失败、依赖阻塞、发布超时、额度压力、存储保护和服务异常 |

**聚合口径。**任务数量、成功率、耗时分位数、队列深度和发布延迟直接由系统运行表按限定时间范围聚合；当前额度和调度心跳由Scheduler本机状态接口提供；数据库状态通过只读系统视图查询；本机资源使用轻量进程内采样。所有接口均返回generated_at，前端按需刷新，不维护另一套时序数据库。

**数据粒度。**列表与详情保留batch_id、task_id、process_id、api_name和dataset等定位字段；概览接口只返回聚合结果，默认查询最近24小时或最近30天，禁止无时间范围扫描运行历史。接口请求参数、Token和完整错误响应不进入监控响应。

| 告警 | 级别 | 触发条件 | 处置入口 |
|-|-|-|-|
| Scheduler不可用 | P1 | 状态接口不可达或last_poll超过3个轮询周期 | 检查单例锁、数据库和进程日志，恢复后执行reconciler |
| Token/权限/schema错误 | P1 | 任一不可恢复供应方错误 | 暂停受影响接口并保留原始文件 |
| 正式数据超时未发布 | P1/P2 | 超过数据集发布目标仍无对应dataset_release | 定位失败任务或依赖并创建REPAIR |
| 加工阻塞 | P2 | BLOCKED超过30分钟或最老排队超过2小时 | 检查缺失资产、上游发布或分区 |
| Tushare异常 | P2 | 10分钟错误率超过20%且请求数不少于20 | 暂停低优先级接口并检查供应方状态 |
| 额度压力 | P3 | 当前窗口使用量持续接近配置预算 | 检查异常拆分或重试风暴 |
| 存储保护 | P1/P2 | 触及预警线或绝对保留空间 | 按反压规则暂停任务并执行受控归档 |

**管理端页面。**现有DashboardView、RunRecordsView、AlertsView和SystemView作为统一监控入口。页面只调用FastAPI，不直连PostgreSQL或Scheduler；任务详情继续使用系统运行表，当前限流与资源状态由后端聚合后返回。

**日志。**API和Scheduler输出JSON日志，统一包含timestamp、level、service、event、request_id以及可用的batch_id、task_id、process_id、api_name和dataset。Token、敏感请求参数、数据库口令和完整响应内容必须脱敏。日志按大小轮转并设置保留份数；告警详情可携带对应request_id，但监控接口不扫描整份日志生成统计。

