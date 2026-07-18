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
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.observability.metrics import SCHEDULED_JOBS
from app.scheduler.jobs import dispatch_due_tasks


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
        dispatch_due_tasks,
        trigger=IntervalTrigger(seconds=settings.scheduler_poll_seconds),
        id="dispatch-due-tasks",
        name="扫描待执行任务",
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
