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
from app.scheduler.jobs import (
    close_collection_batches,
    dispatch_collection_tasks,
    dispatch_processing_task,
    ensure_future_partitions,
    plan_daily_close,
    plan_daily_final,
    plan_daily_late,
    plan_daily_preopen,
    plan_next_year_trade_calendar,
    plan_processing_tasks,
    plan_stock_master,
    plan_trade_calendar,
    reconcile_collection_runtime,
    reconcile_processing_runtime,
)


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
        dispatch_collection_tasks,
        trigger=IntervalTrigger(seconds=5),
        id="dispatch-collection-tasks",
        name="派发采集任务",
        replace_existing=True,
    )
    scheduler.add_job(
        close_collection_batches,
        trigger=IntervalTrigger(seconds=settings.scheduler_poll_seconds),
        id="close-collection-batches",
        name="关闭已终态采集批次",
        replace_existing=True,
    )
    scheduler.add_job(
        plan_processing_tasks,
        trigger=IntervalTrigger(seconds=settings.scheduler_poll_seconds),
        id="plan-processing-tasks",
        name="规划已关闭批次加工任务",
        replace_existing=True,
    )
    scheduler.add_job(
        dispatch_processing_task,
        trigger=IntervalTrigger(seconds=5),
        id="dispatch-processing-task",
        name="派发全局串行加工任务",
        replace_existing=True,
    )
    scheduler.add_job(
        reconcile_collection_runtime,
        trigger=IntervalTrigger(minutes=5),
        id="reconcile-collection-runtime",
        name="协调采集运行状态",
        replace_existing=True,
    )
    scheduler.add_job(
        reconcile_processing_runtime,
        trigger=IntervalTrigger(minutes=5),
        id="reconcile-processing-runtime",
        name="协调加工运行状态",
        replace_existing=True,
    )
    scheduler.add_job(
        plan_trade_calendar,
        trigger=CronTrigger(day=1, hour=8, minute=20),
        id="plan-trade-calendar",
        name="规划交易日历采集",
        replace_existing=True,
    )
    scheduler.add_job(
        plan_stock_master,
        trigger=CronTrigger(day=1, hour=8, minute=30),
        id="plan-stock-master",
        name="规划股票主数据采集",
        replace_existing=True,
    )
    scheduler.add_job(
        plan_next_year_trade_calendar,
        trigger=CronTrigger(month="10-12", day=1, hour=8, minute=25),
        id="plan-next-year-trade-calendar",
        name="规划下一年度交易日历采集",
        replace_existing=True,
    )
    for job_function, hour, minute, job_id, name in (
        (plan_daily_preopen, 9, 25, "plan-daily-preopen", "规划盘前采集"),
        (plan_daily_close, 16, 10, "plan-daily-close", "规划收盘采集"),
        (plan_daily_late, 17, 30, "plan-daily-late", "规划盘后采集"),
        (plan_daily_final, 19, 0, "plan-daily-final", "冻结每日采集计划"),
    ):
        scheduler.add_job(
            job_function,
            trigger=CronTrigger(hour=hour, minute=minute),
            id=job_id,
            name=name,
            replace_existing=True,
        )
    scheduler.add_job(
        ensure_future_partitions,
        trigger=CronTrigger(hour=8, minute=30),
        id="ensure-future-partitions",
        name="检查未来月份分区",
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
