<!-- 来源：https://tcnq6fudd3wh.feishu.cn/docx/WoYqdWMeJoqcOtxUVJxccyjenTe；飞书修订版：88 -->

# 9. 统一入口与管理API

系统提供运行进程入口、管理入口和数据消费入口，但所有改变外部或正式数据状态的动作最终都必须落到同一任务服务，不能形成第二条执行链。

| 入口 | 用途 | 执行边界 |
|-|-|-|
| uvicorn app.main:app | 管理API、数据查询API、健康检查、API进程指标 | 只做短请求，不执行采集和加工 |
| python -m app.scheduler.runner | 生产唯一调度和执行进程 | 持有调度单例锁，运行采集与串行加工执行器 |
| python -m app.cli | 服务器侧诊断、补采、回填和恢复 | 复用应用Service创建数据库任务，不直接调用Provider |
| Vue管理端 | 可视化查询和受控人工操作 | 只通过FastAPI |
| 研究消费入口 | 业务API或只读数据库账号 | 只访问正式表，并依据dataset_release判断完整性 |

**管理查询API。**建议提供GET /operations/overview、/batches、/collection-tasks、/raw-assets、/processing-tasks、/dependencies、/releases、/providers/tushare、/alerts和/system。列表统一支持状态、接口、数据集、业务日期、批次和分页过滤，详情返回关联ID和可追溯链接。

**人工操作API。**建议提供POST /tasks/backfills、/batches/{id}/cancel、/collection-tasks/{id}/retry、/processing-tasks/{id}/retry、/processing-tasks/{id}/skip和/processing-tasks/{id}/cancel。每个写请求必须提交reason和Idempotency-Key；服务端只允许合法状态迁移，并返回已创建或复用的batch_id、task_id或process_id。

BACKFILL根据数据集、开始日期、结束日期和可选代码范围创建低优先级批次；REPAIR针对失败或缺失依赖创建新批次；processing retry只重放已有raw_data_asset。人工操作不能修改sealed资产、不能直接UPDATE业务表、不能把BLOCKED任务强制改成SUCCESS。

**请求幂等。**人工批次的batch_id由Idempotency-Key和规范化请求计算UUIDv5；相同请求重复提交返回同一资源。状态迁移使用数据库条件更新，例如只允许FAILED或RETRY_WAIT进入PENDING/QUEUED，更新行数为0时返回409。

**管理鉴权。**单机单管理员阶段，写接口使用独立ADMIN_API_TOKEN并由Nginx限制来源；每次操作记录request_id、动作、目标ID、reason、结果和客户端地址到结构化审计日志。本期不新增审计表。若以后支持多用户、审批或不可抵赖审计，再增加账号体系和持久审计表，不能继续只依赖单一Token。

**消费门禁。**业务查询Service先按数据集和范围读取dataset_release；未发布返回“数据尚未完整”，不把空查询结果解释为无行情。原始Parquet只供加工与运维恢复，不通过研究API开放。

