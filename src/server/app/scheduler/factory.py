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
from app.scheduler.catalog import SCHEDULED_JOB_BY_ID
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
        name=_job_name("dispatch-collection-tasks"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=IntervalTrigger(seconds=settings.scheduler_poll_seconds),
        args=("close-collection-batches", "SCHEDULED"),
        id="close-collection-batches",
        name=_job_name("close-collection-batches"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=IntervalTrigger(seconds=settings.scheduler_poll_seconds),
        args=("plan-processing-tasks", "SCHEDULED"),
        id="plan-processing-tasks",
        name=_job_name("plan-processing-tasks"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=IntervalTrigger(seconds=5),
        args=("dispatch-processing-task", "SCHEDULED"),
        id="dispatch-processing-task",
        name=_job_name("dispatch-processing-task"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=IntervalTrigger(minutes=5),
        args=("reconcile-collection-runtime", "SCHEDULED"),
        id="reconcile-collection-runtime",
        name=_job_name("reconcile-collection-runtime"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=IntervalTrigger(minutes=5),
        args=("reconcile-processing-runtime", "SCHEDULED"),
        id="reconcile-processing-runtime",
        name=_job_name("reconcile-processing-runtime"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=8, minute=20),
        args=("plan-trade-calendar", "SCHEDULED"),
        id="plan-trade-calendar",
        name=_job_name("plan-trade-calendar"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=8, minute=30),
        args=("plan-stock-master", "SCHEDULED"),
        id="plan-stock-master",
        name=_job_name("plan-stock-master"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=8, minute=35),
        args=("plan-etf-master", "SCHEDULED"),
        id="plan-etf-master",
        name=_job_name("plan-etf-master"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=8, minute=40),
        args=("plan-special-master", "SCHEDULED"),
        id="plan-special-master",
        name=_job_name("plan-special-master"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=10),
        args=("plan-concept-board-members", "SCHEDULED"),
        id="plan-concept-board-members",
        name=_job_name("plan-concept-board-members"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(day=2, hour=8, minute=50),
        args=("plan-monthly-index-weights", "SCHEDULED"),
        id="plan-monthly-index-weights",
        name=_job_name("plan-monthly-index-weights"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(month="10-12", day=1, hour=8, minute=25),
        args=("plan-next-year-trade-calendar", "SCHEDULED"),
        id="plan-next-year-trade-calendar",
        name=_job_name("plan-next-year-trade-calendar"),
        replace_existing=True,
    )
    for hour, minute, job_id in (
        (9, 25, "plan-daily-preopen"),
        (16, 10, "plan-daily-close"),
        (17, 30, "plan-daily-late"),
        (19, 0, "plan-daily-final"),
    ):
        scheduler.add_job(
            execute_scheduled_job,
            trigger=CronTrigger(hour=hour, minute=minute),
            args=(job_id, "SCHEDULED"),
            id=job_id,
            name=_job_name(job_id),
            replace_existing=True,
        )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=8, minute=45),
        args=("plan-etf-share-size", "SCHEDULED"),
        id="plan-etf-share-size",
        name=_job_name("plan-etf-share-size"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour="20-21", minute="*/10"),
        args=("plan-theme-members", "SCHEDULED"),
        id="plan-theme-members",
        name=_job_name("plan-theme-members"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=22, minute=35),
        args=("plan-hot-rank", "SCHEDULED"),
        id="plan-hot-rank",
        name=_job_name("plan-hot-rank"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=8, minute="0,15,30,45"),
        args=("check-previous-day-data-sync", "SCHEDULED"),
        id="check-previous-day-data-sync",
        name=_job_name("check-previous-day-data-sync"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=8, minute=30),
        args=("ensure-future-partitions", "SCHEDULED"),
        id="ensure-future-partitions",
        name=_job_name("ensure-future-partitions"),
        replace_existing=True,
    )
    scheduler.add_job(
        execute_scheduled_job,
        trigger=CronTrigger(hour=3, minute=10),
        args=("cleanup-scheduled-job-executions", "SCHEDULED"),
        id="cleanup-scheduled-job-executions",
        name=_job_name("cleanup-scheduled-job-executions"),
        replace_existing=True,
    )
    scheduler.add_job(
        dispatch_manual_scheduled_job,
        trigger=IntervalTrigger(seconds=5),
        id="dispatch-manual-scheduled-jobs",
        name="派发人工调度请求",
        max_instances=settings.scheduler_max_workers - 1,
        replace_existing=True,
    )
    return scheduler


def _job_name(job_id: str) -> str:
    return SCHEDULED_JOB_BY_ID[job_id].name


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
