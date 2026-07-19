<!-- 来源：https://tcnq6fudd3wh.feishu.cn/docx/WoYqdWMeJoqcOtxUVJxccyjenTe；飞书修订版：88 -->

# 11. 故障恢复、补采与备份

| 故障场景 | 系统行为 | 恢复依据 |
|-|-|-|
| Scheduler进程退出 | advisory lock自动释放；进程重新启动后先执行reconciler再恢复派发 | 数据库任务状态、封存资产和dataset_release |
| 采集运行中崩溃 | 按临时文件、最终文件、资产记录和任务状态四项组合协调；仅全部一致才确认SUCCESS，否则补登记、隔离或转RETRY_WAIT | task_id唯一的raw_data_asset和最终文件 |
| 加工运行中崩溃 | 若release已指向output_version则补记SUCCESS；否则依赖PostgreSQL回滚后重新QUEUED | dataset_release、事务原子性和加工锁 |
| 数据库暂不可用 | 停止领取新任务，已有外部请求结果不得绕过数据库封存 | 数据库恢复后重试，禁止在内存中宣称完成 |
| Parquet磁盘写满 | 当前采集失败，不创建资产；按容量阈值启动反压 | 临时目录、任务错误和磁盘监控 |
| Tushare迟到或空结果 | 在截止时间前退避重试；原批次关闭后进入REPAIR | ApiSpec empty_policy和业务发布时间 |
| 供应方修订历史数据 | 创建新资产和新output_version，只重算受影响范围 | 资产哈希、处理器版本和依赖图 |
| 分区缺失 | 加工BLOCKED，不落DEFAULT分区 | partition_months_ahead和reconciler |

**启动恢复顺序。**取得调度单例锁后，先检查数据库迁移版本和分区；清理超过24小时的临时原始文件；核对RUNNING采集和加工任务；重新计算可关闭批次；解析已关闭批次中缺失的加工计划；最后才启动新的领取循环。

**补采。**自动补采只针对可恢复失败并有次数和时间上限。原批次关闭前在同一任务上重试；关闭后新建REPAIR批次。修复成功后重新解析受影响依赖和输出范围，不触发无关数据集重算。人工历史回填先由trade_calendar枚举开市日，按日期分批创建BACKFILL，优先级低于日常任务。

**备份范围。**PostgreSQL每日执行pg_dump自定义格式，原始Parquet按不可变文件增量同步，配置文件只备份加密副本，内置监控规则和页面配置随代码版本管理。备份完成后生成清单和SHA-256，并复制到主机之外的存储；只保存在同一块磁盘不能视为备份。

| 备份项 | 频率 | 保留建议 | 恢复验证 |
|-|-|-|-|
| PostgreSQL逻辑备份 | 每日一次 | 7个日备、4个周备、12个月备 | 每月在隔离库执行恢复和行数核对 |
| Parquet原始资产 | 每日增量 | 与生产资产同生命周期 | 抽样核对content_hash并重放一个数据集 |
| 配置和监控规则 | 每次发布 | 跟随发布版本 | 新环境可从发布包和密钥恢复 |

**恢复目标。**单机方案建议RPO不超过24小时、RTO不超过4小时。恢复顺序为PostgreSQL、raw资产、迁移校验、只读API、Scheduler恢复、Web和监控；Scheduler启动后先做一致性恢复，不直接追赶全部历史阶段。

# 12. 安全、配置与权限

本期按内部单管理员系统设计，但仍要保证密钥不泄露、正式数据不可被浏览器直接改写、数据库权限最小化。若对公网开放，必须先完成TLS、访问控制和管理员鉴权。

| 角色/配置 | 权限或规则 |
|-|-|
| 数据库迁移账号 | 拥有DDL和对象所有权，只在migrate任务中使用 |
| 应用读写账号 | API和Scheduler使用；只允许所需表DML、序列和函数 |
| 研究只读账号 | 只读正式业务表和dataset_release，不读运行请求参数 |
| 内置监控查询 | operations/system模块通过应用只读连接查询数据库状态，不授予额外写权限 |
| TUSHARE_TOKEN | 仅存在权限0600的app.env或外部Secret，不写数据库、不写日志、不返回前端 |
| ADMIN_API_TOKEN | 只保护管理写接口，与Tushare Token分离并支持轮换 |
| DATABASE_URL | 不输出完整值；启动日志只显示数据库主机和库名 |

**网络。**PostgreSQL、API内部监听和Scheduler状态端口仅绑定本机回环地址或Unix Socket；外部请求只进入Web反向代理。反向代理为API设置请求体、超时和来源限制，管理写接口不允许跨域通配。对公网时使用受信任证书，并关闭FastAPI调试模式和公开的管理OpenAPI。

**配置校验。**启动时校验Tushare全局预算、接口预算和日配额不超过账户配置，时区固定Asia/Shanghai，加工并发严格等于1，原始目录可写，未来分区数量满足要求，数据库迁移版本一致。关键配置不合法时进程失败退出，不能带错误默认值继续运行。

**数据权限。**研究消费者不能读取collection_task.request_params和raw_data_asset.storage_uri；管理端展示请求参数时默认脱敏。正式表的直接写权限只授予Scheduler使用的应用角色，API若与Scheduler共用账号，则Service层仍禁止提供任意SQL和任意正式表更新接口。

**人工操作。**取消、跳过、重试和回填都要求reason，日志记录操作者身份和request_id。删除原始资产、删除分区、覆盖备份和回滚迁移属于高风险运维动作，不通过普通Web API提供。

