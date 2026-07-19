from datetime import UTC, datetime
from time import monotonic
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.sync_session import SyncSessionFactory
from app.modules.operations.models import ScheduledJobControl, ScheduledJobExecution
from app.scheduler.catalog import SCHEDULED_JOB_BY_ID, SCHEDULED_JOB_DEFINITIONS


def ensure_scheduled_job_controls() -> None:
    now = datetime.now(UTC)
    with SyncSessionFactory() as session, session.begin():
        for definition in SCHEDULED_JOB_DEFINITIONS:
            session.execute(
                insert(ScheduledJobControl)
                .values(job_id=definition.job_id, enabled=True, updated_at=now)
                .on_conflict_do_nothing(index_elements=(ScheduledJobControl.job_id,))
            )


def execute_scheduled_job(
    job_id: str,
    trigger_type: str = "SCHEDULED",
    execution_id: str | None = None,
) -> bool:
    if job_id not in SCHEDULED_JOB_BY_ID:
        raise KeyError(f"unknown scheduled job: {job_id}")
    if trigger_type != "MANUAL" and not _job_enabled(job_id):
        structlog.get_logger("scheduler").info("scheduled_job_disabled", job_id=job_id)
        return False

    identifier = UUID(execution_id) if execution_id else uuid4()
    started_at = datetime.now(UTC)
    with SyncSessionFactory() as session, session.begin():
        execution = session.get(ScheduledJobExecution, identifier)
        if execution is None:
            execution = ScheduledJobExecution(
                execution_id=identifier,
                job_id=job_id,
                trigger_type=trigger_type,
                status="RUNNING",
                scheduled_at=started_at,
                started_at=started_at,
                created_at=started_at,
            )
            session.add(execution)
        else:
            execution.status = "RUNNING"
            execution.started_at = started_at
            execution.error_message = None

    clock_started = monotonic()
    try:
        from app.scheduler.jobs import registered_job_functions

        registered_job_functions()[job_id]()
    except Exception as exc:
        _finish_execution(
            identifier,
            status="FAILED",
            duration_ms=round((monotonic() - clock_started) * 1000),
            error_message=str(exc)[:2000],
        )
        raise

    _finish_execution(
        identifier,
        status="SUCCESS",
        duration_ms=round((monotonic() - clock_started) * 1000),
        error_message=None,
    )
    return True


def dispatch_manual_scheduled_job() -> None:
    with SyncSessionFactory() as session, session.begin():
        execution = session.scalar(
            select(ScheduledJobExecution)
            .where(ScheduledJobExecution.status == "PENDING")
            .order_by(ScheduledJobExecution.created_at, ScheduledJobExecution.execution_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if execution is None:
            return
        execution.status = "RUNNING"
        execution.started_at = datetime.now(UTC)
        execution_id = str(execution.execution_id)
        job_id = execution.job_id

    execute_scheduled_job(job_id, "MANUAL", execution_id)


def _job_enabled(job_id: str) -> bool:
    with SyncSessionFactory() as session:
        enabled = session.scalar(
            select(ScheduledJobControl.enabled).where(ScheduledJobControl.job_id == job_id)
        )
    return True if enabled is None else bool(enabled)


def _finish_execution(
    execution_id: UUID,
    *,
    status: str,
    duration_ms: int,
    error_message: str | None,
) -> None:
    with SyncSessionFactory() as session, session.begin():
        execution = session.get(ScheduledJobExecution, execution_id)
        if execution is None:
            raise RuntimeError(f"scheduled execution disappeared: {execution_id}")
        execution.status = status
        execution.finished_at = datetime.now(UTC)
        execution.duration_ms = duration_ms
        execution.error_message = error_message
