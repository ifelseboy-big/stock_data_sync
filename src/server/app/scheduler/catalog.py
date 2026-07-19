from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScheduledJobDefinition:
    job_id: str
    name: str
    category: str
    schedule: str
    manual_allowed: bool = True


SCHEDULED_JOB_DEFINITIONS = (
    ScheduledJobDefinition("dispatch-collection-tasks", "派发采集任务", "runtime", "每 5 秒"),
    ScheduledJobDefinition("close-collection-batches", "关闭已终态采集批次", "runtime", "每 30 秒"),
    ScheduledJobDefinition(
        "plan-processing-tasks", "规划已关闭批次加工任务", "runtime", "每 30 秒"
    ),
    ScheduledJobDefinition(
        "dispatch-processing-task", "派发全局串行加工任务", "runtime", "每 5 秒"
    ),
    ScheduledJobDefinition(
        "reconcile-collection-runtime", "协调采集运行状态", "runtime", "每 5 分钟"
    ),
    ScheduledJobDefinition(
        "reconcile-processing-runtime", "协调加工运行状态", "runtime", "每 5 分钟"
    ),
    ScheduledJobDefinition("plan-trade-calendar", "规划交易日历采集", "master", "每月 1 日 08:20"),
    ScheduledJobDefinition("plan-stock-master", "规划股票主数据采集", "master", "每月 1 日 08:30"),
    ScheduledJobDefinition("plan-etf-master", "规划 ETF 主数据采集", "master", "每月 1 日 08:35"),
    ScheduledJobDefinition(
        "plan-special-master", "规划概念、主题和指数主数据采集", "master", "每月 1 日 08:40"
    ),
    ScheduledJobDefinition(
        "plan-concept-board-members", "规划同花顺概念和主题成分采集", "master", "每月 1 日 10:00"
    ),
    ScheduledJobDefinition(
        "plan-monthly-index-weights", "规划月度指数权重采集", "master", "每月 2 日 08:50"
    ),
    ScheduledJobDefinition(
        "plan-next-year-trade-calendar",
        "规划下一年度交易日历采集",
        "master",
        "10–12 月每月 1 日 08:25",
    ),
    ScheduledJobDefinition("plan-daily-preopen", "规划盘前采集", "daily", "每日 09:25"),
    ScheduledJobDefinition("plan-daily-close", "规划收盘采集", "daily", "每日 16:10"),
    ScheduledJobDefinition("plan-daily-late", "规划盘后采集", "daily", "每日 17:30"),
    ScheduledJobDefinition("plan-daily-final", "冻结每日采集计划", "daily", "每日 19:00"),
    ScheduledJobDefinition("plan-etf-share-size", "规划 ETF 份额规模采集", "daily", "每日 08:45"),
    ScheduledJobDefinition("plan-theme-members", "规划题材成分采集", "daily", "20–21 点每 10 分钟"),
    ScheduledJobDefinition("plan-hot-rank", "规划最终热榜采集", "daily", "每日 22:35"),
    ScheduledJobDefinition(
        "ensure-future-partitions", "检查未来月份分区", "maintenance", "每日 08:30"
    ),
    ScheduledJobDefinition(
        "cleanup-scheduled-job-executions",
        "清理过期调度执行记录",
        "maintenance",
        "每日 03:10",
    ),
)

SCHEDULED_JOB_BY_ID = {item.job_id: item for item in SCHEDULED_JOB_DEFINITIONS}
