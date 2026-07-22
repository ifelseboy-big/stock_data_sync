from datetime import UTC, datetime
from time import monotonic
from uuid import UUID, uuid4

import structlog
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.sync_session import SyncSessionFactory
from app.modules.operations.models import ScheduledJobControl, ScheduledJobExecution
from app.scheduler.catalog import SCHEDULED_JOB_BY_ID, SCHEDULED_JOB_DEFINITIONS

scheduler_job_lock_engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    poolclass=NullPool,
)


def ensure_scheduled_job_controls() -> None:
    now = datetime.now(UTC)
    with SyncSessionFactory() as session, session.begin():
        for definition in SCHEDULED_JOB_DEFINITIONS:
            session.execute(
                insert(ScheduledJobControl)
                .values(job_id=definition.job_id, enabled=True, updated_at=now)
                .on_conflict_do_nothing(index_elements=(ScheduledJobControl.job_id,))
            )


def recover_interrupted_scheduled_job_executions() -> int:
    now = datetime.now(UTC)
    with SyncSessionFactory() as session, session.begin():
        executions = tuple(
            session.scalars(
                select(ScheduledJobExecution)
                .where(ScheduledJobExecution.status == "RUNNING")
                .order_by(ScheduledJobExecution.job_id, ScheduledJobExecution.execution_id)
                .with_for_update()
            )
        )
        for execution in executions:
            _mark_interrupted_execution(execution, now=now)
        return len(executions)


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

    with scheduler_job_lock_engine.connect() as lock_connection:
        acquired = lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"scheduled-job:{job_id}"},
        )
        lock_connection.commit()
        if not acquired:
            structlog.get_logger("scheduler").info(
                "scheduled_job_already_running",
                job_id=job_id,
                trigger_type=trigger_type,
            )
            return False

        identifier = UUID(execution_id) if execution_id else uuid4()
        started_at = datetime.now(UTC)
        try:
            with SyncSessionFactory() as session, session.begin():
                interrupted = tuple(
                    session.scalars(
                        select(ScheduledJobExecution)
                        .where(
                            ScheduledJobExecution.job_id == job_id,
                            ScheduledJobExecution.status == "RUNNING",
                        )
                        .order_by(ScheduledJobExecution.execution_id)
                        .with_for_update()
                    )
                )
                for stale_execution in interrupted:
                    _mark_interrupted_execution(stale_execution, now=started_at)
                if interrupted:
                    session.flush()
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
                elif execution.status != "PENDING":
                    return False
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
        finally:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"scheduled-job:{job_id}"},
            )
            lock_connection.commit()


def dispatch_manual_scheduled_job() -> None:
    with SyncSessionFactory() as session, session.begin():
        executions = tuple(
            session.scalars(
                select(ScheduledJobExecution)
                .where(
                    ScheduledJobExecution.status == "PENDING",
                    ScheduledJobExecution.job_id.in_(tuple(SCHEDULED_JOB_BY_ID)),
                )
                .order_by(ScheduledJobExecution.created_at, ScheduledJobExecution.execution_id)
                .with_for_update(skip_locked=True)
            )
        )
        candidates = tuple(
            (execution.job_id, str(execution.execution_id)) for execution in executions
        )

    for job_id, execution_id in candidates:
        if execute_scheduled_job(job_id, "MANUAL", execution_id):
            return


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


def _mark_interrupted_execution(
    execution: ScheduledJobExecution,
    *,
    now: datetime,
) -> None:
    started_at = execution.started_at or execution.created_at
    execution.status = "FAILED"
    execution.finished_at = now
    execution.duration_ms = max(
        0,
        min(round((now - started_at).total_seconds() * 1000), 2_147_483_647),
    )
    execution.error_message = "scheduler process stopped before execution completed"
