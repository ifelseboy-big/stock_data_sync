from zoneinfo import ZoneInfo

import structlog
from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
)
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.observability.metrics import SCHEDULED_JOBS
from app.scheduler.management import dispatch_manual_scheduled_job, execute_scheduled_job


def create_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(
        jobstores={
            "default": SQLAlchemyJobStore(
                url=settings.database_url,
                tablename=settings.scheduler_jobstore_table,
                engine_options={"pool_pre_ping": True},
            )
        },
        executors={"default": ThreadPoolExecutor(max_workers=settings.scheduler_max_workers)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        timezone=ZoneInfo(settings.scheduler_timezone),
    )
    scheduler.add_listener(
        _record_job_event,
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=IntervalTrigger(seconds=5),
        args=("dispatch-collection-tasks", "SCHEDULED"),
        id="dispatch-collection-tasks",
        name="派发采集任务",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=IntervalTrigger(seconds=settings.scheduler_poll_seconds),
        args=("close-collection-batches", "SCHEDULED"),
        id="close-collection-batches",
        name="关闭已终态采集批次",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=IntervalTrigger(seconds=settings.scheduler_poll_seconds),
        args=("plan-processing-tasks", "SCHEDULED"),
        id="plan-processing-tasks",
        name="规划已关闭批次加工任务",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=IntervalTrigger(seconds=5),
        args=("dispatch-processing-task", "SCHEDULED"),
        id="dispatch-processing-task",
        name="派发全局串行加工任务",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=IntervalTrigger(minutes=5),
        args=("reconcile-collection-runtime", "SCHEDULED"),
        id="reconcile-collection-runtime",
        name="协调采集运行状态",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=IntervalTrigger(minutes=5),
        args=("reconcile-processing-runtime", "SCHEDULED"),
        id="reconcile-processing-runtime",
        name="协调加工运行状态",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(day=1, hour=8, minute=20),
        args=("plan-trade-calendar", "SCHEDULED"),
        id="plan-trade-calendar",
        name="规划交易日历采集",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(day=1, hour=8, minute=30),
        args=("plan-stock-master", "SCHEDULED"),
        id="plan-stock-master",
        name="规划股票主数据采集",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(day=1, hour=8, minute=35),
        args=("plan-etf-master", "SCHEDULED"),
        id="plan-etf-master",
        name="规划ETF主数据采集",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(day=1, hour=8, minute=40),
        args=("plan-special-master", "SCHEDULED"),
        id="plan-special-master",
        name="规划概念、主题和指数主数据采集",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(day=1, hour=10),
        args=("plan-concept-board-members", "SCHEDULED"),
        id="plan-concept-board-members",
        name="规划同花顺概念和主题成分采集",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(day=2, hour=8, minute=50),
        args=("plan-monthly-index-weights", "SCHEDULED"),
        id="plan-monthly-index-weights",
        name="规划月度指数权重采集",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(month="10-12", day=1, hour=8, minute=25),
        args=("plan-next-year-trade-calendar", "SCHEDULED"),
        id="plan-next-year-trade-calendar",
        name="规划下一年度交易日历采集",
        replace_existing=True,
    )
    for hour, minute, job_id, name in (
        (9, 25, "plan-daily-preopen", "规划盘前采集"),
        (16, 10, "plan-daily-close", "规划收盘采集"),
        (17, 30, "plan-daily-late", "规划盘后采集"),
        (19, 0, "plan-daily-final", "冻结每日采集计划"),
    ):
        scheduler.add_job(
            execute_scheduled_job,
            trigger=CronTrigger(hour=hour, minute=minute),
            args=(job_id, "SCHEDULED"),
            id=job_id,
            name=name,
            replace_existing=True,
        )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=8, minute=45),
        args=("plan-etf-share-size", "SCHEDULED"),
        id="plan-etf-share-size",
        name="规划ETF份额规模采集",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour="20-21", minute="*/10"),
        args=("plan-theme-members", "SCHEDULED"),
        id="plan-theme-members",
        name="规划题材成分采集",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=22, minute=35),
        args=("plan-hot-rank", "SCHEDULED"),
        id="plan-hot-rank",
        name="规划最终热榜采集",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=8, minute=30),
        args=("ensure-future-partitions", "SCHEDULED"),
        id="ensure-future-partitions",
        name="检查未来月份分区",
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=3, minute=10),
        args=("cleanup-scheduled-job-executions", "SCHEDULED"),
        id="cleanup-scheduled-job-executions",
        name="清理过期调度执行记录",
        replace_existing=True,
    )
    scheduler.add_job(
        dispatch_manual_scheduled_job,
        trigger=IntervalTrigger(seconds=5),
        id="dispatch-manual-scheduled-jobs",
        name="派发人工调度请求",
        replace_existing=True,
    )
    return scheduler


def _record_job_event(event: JobExecutionEvent) -> None:
    if event.code == EVENT_JOB_EXECUTED:
        status = "success"
    elif event.code == EVENT_JOB_MISSED:
        status = "missed"
    else:
        status = "error"

    SCHEDULED_JOBS.labels(job_id=event.job_id, status=status).inc()
    structlog.get_logger("scheduler").info(
        "job_finished",
        job_id=event.job_id,
        status=status,
        exception=str(event.exception) if event.exception else None,
    )
