from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScheduledJobDefinition:
    job_id: str
    name: str
    description: str
    category: str
    schedule: str
    manual_allowed: bool = True


SCHEDULED_JOB_DEFINITIONS = (
    ScheduledJobDefinition(
        "dispatch-collection-tasks",
        "执行待采集接口",
        "按修复、日常、历史回填的优先级领取任务，调用 Tushare 并封存原始数据。",
        "runtime",
        "每 5 秒",
    ),
    ScheduledJobDefinition(
        "close-collection-batches",
        "汇总并关闭采集批次",
        "检查批次内所有接口结果，达到终态后关闭批次并允许进入清洗阶段。",
        "runtime",
        "每 30 秒",
    ),
    ScheduledJobDefinition(
        "plan-processing-tasks",
        "生成清洗入库任务",
        "根据已关闭批次和数据集依赖，生成清洗、聚合及正式表发布任务。",
        "runtime",
        "每 30 秒",
    ),
    ScheduledJobDefinition(
        "dispatch-processing-task",
        "串行执行清洗入库",
        "全局一次只执行一个加工任务，完成清洗、质量校验、正式表写入和发布。",
        "runtime",
        "每 5 秒",
    ),
    ScheduledJobDefinition(
        "reconcile-collection-runtime",
        "恢复异常采集任务",
        "处理超时采集、孤儿临时文件和未登记原始资产，恢复可重试任务。",
        "runtime",
        "每 5 分钟",
    ),
    ScheduledJobDefinition(
        "reconcile-processing-runtime",
        "恢复异常加工任务",
        "识别超时加工任务并恢复到可重试状态，避免队列长期占用。",
        "runtime",
        "每 5 分钟",
    ),
    ScheduledJobDefinition(
        "plan-trade-calendar",
        "同步交易日历",
        "采集沪深交易所开休市日期，供每日任务和历史回填判断交易日。",
        "master",
        "每日 08:20",
    ),
    ScheduledJobDefinition(
        "plan-stock-master",
        "同步股票列表与公司资料",
        "采集股票代码、名称、上市状态及上市公司基础资料。",
        "master",
        "每日 08:30",
    ),
    ScheduledJobDefinition(
        "plan-etf-master",
        "同步 ETF 列表",
        "采集 ETF 代码、名称、跟踪指数、管理人和上市状态。",
        "master",
        "每日 08:35",
    ),
    ScheduledJobDefinition(
        "plan-special-master",
        "同步概念、主题与市场指数",
        "采集同花顺概念板块、主题指数以及上证、深证、创业板等市场指数列表。",
        "master",
        "每日 08:40",
    ),
    ScheduledJobDefinition(
        "plan-concept-board-members",
        "同步同花顺概念与主题成分",
        "逐个采集同花顺概念板块和主题指数包含的股票。",
        "master",
        "每日 10:00",
    ),
    ScheduledJobDefinition(
        "plan-monthly-index-weights",
        "同步指数成分权重",
        "采集主要市场指数当月成分股及权重快照。",
        "master",
        "每月 2 日 08:50",
    ),
    ScheduledJobDefinition(
        "plan-next-year-trade-calendar",
        "预同步下一年度交易日历",
        "在年末提前采集下一年度沪深交易日历，避免跨年任务缺少日期门禁。",
        "master",
        "10–12 月每月 1 日 08:25",
    ),
    ScheduledJobDefinition(
        "plan-daily-preopen",
        "采集股票复权因子",
        "盘前采集股票复权因子，为当日股票行情复权计算做准备。",
        "daily",
        "每日 09:25",
    ),
    ScheduledJobDefinition(
        "plan-daily-close",
        "采集股票与 ETF 收盘行情",
        "收盘后采集股票日线和 ETF 日线的开高低收、成交量及成交额。",
        "daily",
        "每日 16:10",
    ),
    ScheduledJobDefinition(
        "plan-daily-late",
        "采集盘后指标与专题数据",
        "采集估值、资金流、停复牌、指数、概念主题、龙虎榜及涨跌停等盘后数据。",
        "daily",
        "每日 17:30",
    ),
    ScheduledJobDefinition(
        "plan-daily-final",
        "补齐并冻结当日采集批次",
        "补齐遗漏的日频接口，加入股票技术指标任务后冻结当天采集计划。",
        "daily",
        "每日 19:00",
    ),
    ScheduledJobDefinition(
        "plan-etf-share-size",
        "采集 ETF 份额与规模",
        "采集最近交易日 ETF 份额、基金规模、净值和收盘价。",
        "daily",
        "每日 08:45",
    ),
    ScheduledJobDefinition(
        "plan-theme-members",
        "采集东方财富题材成分",
        "在题材列表就绪后，逐个采集当天东方财富动态题材包含的股票。",
        "daily",
        "20–21 点每 10 分钟",
    ),
    ScheduledJobDefinition(
        "plan-hot-rank",
        "采集股票热度排名",
        "采集同花顺和东方财富最终股票热榜并保存当日排名。",
        "daily",
        "每日 22:35",
    ),
    ScheduledJobDefinition(
        "ensure-future-partitions",
        "预建未来月份数据分区",
        "为 6 张大型日事实表预建当前月和未来月份分区，写入时仍会再次兜底检查。",
        "maintenance",
        "每日 08:30",
    ),
    ScheduledJobDefinition(
        "cleanup-scheduled-job-executions",
        "清理过期调度执行记录",
        "删除超过保留天数的调度成功或失败记录，不处理待执行和运行中记录。",
        "maintenance",
        "每日 03:10",
    ),
)

SCHEDULED_JOB_BY_ID = {item.job_id: item for item in SCHEDULED_JOB_DEFINITIONS}
