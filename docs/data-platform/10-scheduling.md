<!-- 来源：https://tcnq6fudd3wh.feishu.cn/docx/WoYqdWMeJoqcOtxUVJxccyjenTe；飞书修订版：88 -->

# 7. 调度与批次编排

APScheduler负责在确定时刻触发“计划动作”，PostgreSQL中的collection_batch、collection_task和processing_task才是业务任务真相。调度器取得生命周期级advisory lock 731500001后才能运行；第二个实例启动时必须失败退出。

| 调度作业 | 频率 | 职责 |
|-|-|-|
| calendar_maintenance | 启动时、每月一次 | 确保本地交易日历覆盖当年和下一年 |
| stage_planner | 按每日阶段时刻并在启动后补跑 | 创建或复用批次，幂等追加该阶段应有的采集任务 |
| collection_dispatcher | 每5秒 | 按可用线程领取PENDING和到期RETRY_WAIT任务 |
| batch_closer | 每30秒 | 最终阶段已生成且全部任务终态后关闭批次 |
| processing_planner | 每30秒 | 为已关闭且尚未完成计划解析的批次生成加工任务和依赖 |
| processing_dispatcher | 每5秒 | 领取全局队列中唯一一个QUEUED或到期RETRY_WAIT加工任务 |
| reconciler | 启动时、每5分钟 | 恢复异常RUNNING状态、补建分区、检查孤儿文件和发布一致性 |

| 北京时间 | 批次和任务 | 实现规则 |
|-|-|-|
| 08:45 | DELAYED(PREV_TRADE_DATE(D))：etf_share_size；DAILY(D)：stk_limit | 延迟任务按上一交易日计算并复用；当D开市时首次创建当日DAILY批次 |
| 09:25 | DAILY(D)：adj_factor | 复用08:45批次；不得单独加工发布 |
| 16:10 | DAILY(D)：daily、fund_daily、index_daily | 追加盘后第一阶段任务 |
| 17:30 | DAILY(D)：daily_basic、moneyflow、fund_adj、龙虎榜、涨跌停、停复牌、概念行情和板块资金等 | 各原始接口在额度内并行 |
| 19:00 | DAILY(D)：stk_factor、DC题材、题材成员、THS概念成员 | 这是DAILY最终阶段；成员类按板块或题材拆分 |
| 最终阶段完成后 | 关闭DAILY批次并生成加工计划 | 部分采集失败允许关闭；缺依赖的加工任务保持BLOCKED |
| 22:35 | HOT(D)：ths_hot、dc_hot，is_new=Y | 单独批次，校验rank_time为22:30最终版本 |

**批次关闭。**最终阶段必须在同一事务中完成全部任务幂等插入，并写入plan_version、expected_task_count和planning_completed_at。batch_closer仅在计划已冻结、实际任务数等于expected_task_count且全部任务进入终态时关闭批次。批次可带FAILED任务关闭；关闭后不允许新增任务或重新打开，迟到数据必须进入新的REPAIR批次。

**停机补跑。**stage_planner使用固定scheduled_at恢复最近缺失阶段，并先校验本地trade_calendar覆盖有效。ETF延迟任务的业务日期通过“当前日期之前最近一个开市日”计算，不使用自然日减一；重复检查复用同一业务日期的任务。较早缺口转为低优先级BACKFILL，不在启动时无界追赶。休市日跳过当日行情、热榜和题材任务，但继续处理未完成延迟数据、补采和历史回填。

**全局加工锁。**加工执行器除自身单线程外，再使用advisory lock 731500002保护唯一加工槽位。任务按priority、business_date、queued_at和process_id稳定排序。等待重试的任务不持有锁；当前任务释放后，无依赖任务可以继续。

**反压。**当QUEUED加工任务超过200、最老排队时间超过2小时、可用空间触及配置的绝对保留线或数据库/备份异常时，暂停BACKFILL和普通人工重跑；日常和修复任务优先。进入存储保护线后暂停除紧急修复外的新采集，禁止继续写入挤占PostgreSQL保留空间。

