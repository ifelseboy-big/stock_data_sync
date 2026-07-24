from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select

from app.core.config import settings
from app.db.sync_session import SyncSessionFactory
from app.integrations.lark import DataSyncAlert, LarkCliNotifier
from app.modules.acquisition.models import CollectionBatch, CollectionTask
from app.modules.operations.models import ScheduledJobControl, ScheduledJobExecution
from app.modules.processing.models import ProcessingTask

ALERT_JOB_ID = "check-previous-day-data-sync"
REGULAR_DATA_SYNC_JOB_IDS = (
    "plan-trade-calendar",
    "plan-stock-master",
    "plan-etf-master",
    "plan-special-master",
    "plan-concept-board-members",
    "plan-daily-preopen",
    "plan-daily-close",
    "plan-daily-late",
    "plan-daily-final",
    "plan-etf-share-size",
    "plan-theme-members",
    "plan-hot-rank",
)
PRODUCTION_BATCH_TYPES = ("MASTER", "DAILY", "HOT", "DELAYED")


def check_previous_day_data_sync() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    if _already_checked_today(now):
        structlog.get_logger("scheduler").info(
            "previous_day_data_sync_already_checked",
            business_date=(now.date() - timedelta(days=1)).isoformat(),
        )
        return

    alert = build_previous_day_data_sync_alert(now=now)
    if alert.successful:
        structlog.get_logger("scheduler").info(
            "previous_day_data_sync_succeeded",
            business_date=alert.business_date.isoformat(),
        )
        return
    if not settings.lark_alert_enabled:
        structlog.get_logger("scheduler").warning(
            "previous_day_data_sync_alert_not_sent",
            business_date=alert.business_date.isoformat(),
            reason="lark alert is disabled",
        )
        return

    message_id = LarkCliNotifier(settings).send_data_sync_alert(alert)
    structlog.get_logger("scheduler").warning(
        "previous_day_data_sync_alert_sent",
        business_date=alert.business_date.isoformat(),
        message_id=message_id,
    )


def build_previous_day_data_sync_alert(*, now: datetime) -> DataSyncAlert:
    target_date = now.date() - timedelta(days=1)
    start, end = _utc_day_bounds(target_date, ZoneInfo(settings.scheduler_timezone))
    expected_jobs = set(REGULAR_DATA_SYNC_JOB_IDS)
    if target_date.day == 2:
        expected_jobs.add("plan-monthly-index-weights")
    if target_date.month >= 10 and target_date.day == 1:
        expected_jobs.add("plan-next-year-trade-calendar")

    with SyncSessionFactory() as session:
        disabled_jobs = set(
            session.scalars(
                select(ScheduledJobControl.job_id).where(
                    ScheduledJobControl.job_id.in_(expected_jobs),
                    ScheduledJobControl.enabled.is_(False),
                )
            )
        )
        executions = tuple(
            session.scalars(
                select(ScheduledJobExecution)
                .where(
                    ScheduledJobExecution.job_id.in_(expected_jobs),
                    ScheduledJobExecution.created_at >= start,
                    ScheduledJobExecution.created_at < end,
                )
                .order_by(
                    ScheduledJobExecution.job_id,
                    ScheduledJobExecution.created_at,
                    ScheduledJobExecution.execution_id,
                )
            )
        )
        latest_execution = {execution.job_id: execution for execution in executions}
        scheduler_issues = tuple(
            _scheduler_issue(job_id, latest_execution.get(job_id))
            for job_id in sorted(expected_jobs - disabled_jobs)
            if latest_execution.get(job_id) is None or latest_execution[job_id].status != "SUCCESS"
        )

        batches = tuple(
            session.scalars(
                select(CollectionBatch)
                .where(
                    CollectionBatch.batch_type.in_(PRODUCTION_BATCH_TYPES),
                    CollectionBatch.scheduled_at >= start,
                    CollectionBatch.scheduled_at < end,
                )
                .order_by(CollectionBatch.scheduled_at, CollectionBatch.batch_id)
            )
        )
        batch_ids = tuple(batch.batch_id for batch in batches)
        collection_tasks = (
            tuple(
                session.scalars(
                    select(CollectionTask)
                    .where(CollectionTask.batch_id.in_(batch_ids))
                    .order_by(CollectionTask.api_name, CollectionTask.scope_key)
                )
            )
            if batch_ids
            else ()
        )
        processing_tasks = (
            tuple(
                session.scalars(
                    select(ProcessingTask)
                    .where(ProcessingTask.source_batch_id.in_(batch_ids))
                    .order_by(ProcessingTask.output_dataset, ProcessingTask.process_id)
                )
            )
            if batch_ids
            else ()
        )

    issue_details: list[str] = []
    if not batches:
        issue_details.append("前一日没有生成任何生产采集批次")
    for batch in batches:
        if batch.status != "CLOSED":
            issue_details.append(f"批次 {batch.batch_type}/{batch.batch_id} 状态为 {batch.status}")
        elif batch.processing_plan_version is None:
            issue_details.append(f"批次 {batch.batch_type}/{batch.batch_id} 尚未生成加工计划")
    issue_details.extend(
        f"采集 {task.api_name}/{task.scope_key} 状态为 {task.status}"
        for task in collection_tasks
        if task.status not in {"SUCCESS", "EMPTY_VALID"}
    )
    issue_details.extend(
        f"加工 {task.output_dataset} 状态为 {task.status}"
        for task in processing_tasks
        if task.status != "SUCCESS"
    )

    return DataSyncAlert(
        business_date=target_date,
        checked_at=now,
        scheduler_issues=scheduler_issues,
        batch_statuses=dict(Counter(batch.status for batch in batches)),
        collection_statuses=dict(Counter(task.status for task in collection_tasks)),
        processing_statuses=dict(Counter(task.status for task in processing_tasks)),
        issue_details=tuple(issue_details),
    )


def _already_checked_today(now: datetime) -> bool:
    start, end = _utc_day_bounds(now.date(), ZoneInfo(settings.scheduler_timezone))
    with SyncSessionFactory() as session:
        execution = session.scalar(
            select(ScheduledJobExecution.execution_id)
            .where(
                ScheduledJobExecution.job_id == ALERT_JOB_ID,
                ScheduledJobExecution.status == "SUCCESS",
                ScheduledJobExecution.created_at >= start,
                ScheduledJobExecution.created_at < end,
            )
            .limit(1)
        )
    return execution is not None


def _scheduler_issue(
    job_id: str,
    execution: ScheduledJobExecution | None,
) -> str:
    if execution is None:
        return f"{job_id} 未执行"
    detail = f"：{execution.error_message[:160]}" if execution.error_message else ""
    return f"{job_id} 状态为 {execution.status}{detail}"


def _utc_day_bounds(target_date: date, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(target_date, time.min, timezone).astimezone(UTC)
    return start, start + timedelta(days=1)
