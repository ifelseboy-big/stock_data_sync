import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.sync_session import SyncSessionFactory
from app.modules.operations.models import ScheduledJobExecution
from app.scheduler import jobs as scheduler_jobs
from app.scheduler.management import (
    dispatch_manual_scheduled_job,
    execute_scheduled_job,
    recover_interrupted_scheduled_job_executions,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)


def test_same_business_job_is_serialized_across_trigger_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = Event()
    release = Event()

    def blocking_job() -> None:
        entered.set()
        assert release.wait(timeout=10)

    monkeypatch.setattr(
        scheduler_jobs,
        "registered_job_functions",
        lambda: {"plan-processing-tasks": blocking_job},
    )
    pending_id = uuid4()
    now = datetime.now(UTC)
    with SyncSessionFactory() as session, session.begin():
        session.add(
            ScheduledJobExecution(
                execution_id=pending_id,
                job_id="plan-processing-tasks",
                trigger_type="MANUAL",
                status="PENDING",
                scheduled_at=now,
                created_at=now,
            )
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        scheduled = executor.submit(
            execute_scheduled_job,
            "plan-processing-tasks",
            "SCHEDULED",
        )
        assert entered.wait(timeout=10)
        assert (
            execute_scheduled_job(
                "plan-processing-tasks",
                "MANUAL",
                str(pending_id),
            )
            is False
        )
        release.set()
        assert scheduled.result(timeout=10) is True

    assert (
        execute_scheduled_job(
            "plan-processing-tasks",
            "MANUAL",
            str(pending_id),
        )
        is True
    )

    with SyncSessionFactory() as session:
        pending = session.get(ScheduledJobExecution, pending_id)
    assert pending is not None
    assert pending.status == "SUCCESS"
    assert pending.started_at is not None


def test_interrupted_scheduler_recovery_preserves_pending_requests() -> None:
    now = datetime.now(UTC)
    running_id = uuid4()
    pending_id = uuid4()
    job_id = f"test-recovery-{uuid4().hex[:12]}"
    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            (
                ScheduledJobExecution(
                    execution_id=running_id,
                    job_id=job_id,
                    trigger_type="SCHEDULED",
                    status="RUNNING",
                    scheduled_at=now - timedelta(minutes=2),
                    started_at=now - timedelta(minutes=2),
                    created_at=now - timedelta(minutes=2),
                ),
                ScheduledJobExecution(
                    execution_id=pending_id,
                    job_id=job_id,
                    trigger_type="MANUAL",
                    status="PENDING",
                    scheduled_at=now,
                    created_at=now,
                ),
            )
        )

    recovered_count = recover_interrupted_scheduled_job_executions()

    with SyncSessionFactory() as session:
        running = session.get(ScheduledJobExecution, running_id)
        pending = session.get(ScheduledJobExecution, pending_id)
    assert recovered_count >= 1
    assert running is not None
    assert running.status == "FAILED"
    assert running.finished_at is not None
    assert running.error_message == "scheduler process stopped before execution completed"
    assert pending is not None
    assert pending.status == "PENDING"
    with SyncSessionFactory() as session, session.begin():
        for execution_id in (running_id, pending_id):
            persisted = session.get(ScheduledJobExecution, execution_id)
            if persisted is not None:
                session.delete(persisted)


def test_manual_dispatch_skips_busy_head_and_runs_another_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    busy_job_id = "plan-processing-tasks"
    ready_job_id = "close-collection-batches"
    busy_entered = Event()
    release_busy = Event()
    ready_called = Event()

    def blocking_job() -> None:
        busy_entered.set()
        assert release_busy.wait(timeout=10)

    def ready_job() -> None:
        ready_called.set()

    monkeypatch.setattr(
        scheduler_jobs,
        "registered_job_functions",
        lambda: {
            busy_job_id: blocking_job,
            ready_job_id: ready_job,
        },
    )
    busy_pending_id = uuid4()
    ready_pending_id = uuid4()
    now = datetime.now(UTC)
    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            (
                ScheduledJobExecution(
                    execution_id=busy_pending_id,
                    job_id=busy_job_id,
                    trigger_type="MANUAL",
                    status="PENDING",
                    scheduled_at=now - timedelta(minutes=1),
                    created_at=now - timedelta(minutes=1),
                ),
                ScheduledJobExecution(
                    execution_id=ready_pending_id,
                    job_id=ready_job_id,
                    trigger_type="MANUAL",
                    status="PENDING",
                    scheduled_at=now,
                    created_at=now,
                ),
            )
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        scheduled = executor.submit(execute_scheduled_job, busy_job_id, "SCHEDULED")
        assert busy_entered.wait(timeout=10)
        try:
            dispatch_manual_scheduled_job()
            assert ready_called.wait(timeout=10)
        finally:
            release_busy.set()
        assert scheduled.result(timeout=10) is True

    with SyncSessionFactory() as session:
        busy_pending = session.get(ScheduledJobExecution, busy_pending_id)
        ready_pending = session.get(ScheduledJobExecution, ready_pending_id)
    assert busy_pending is not None
    assert busy_pending.status == "PENDING"
    assert ready_pending is not None
    assert ready_pending.status == "SUCCESS"

    with SyncSessionFactory() as session, session.begin():
        executions = tuple(
            session.scalars(
                select(ScheduledJobExecution).where(
                    ScheduledJobExecution.job_id.in_((busy_job_id, ready_job_id))
                )
            )
        )
        for execution in executions:
            session.delete(execution)


def test_two_manual_dispatchers_run_different_jobs_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_job_id = "plan-processing-tasks"
    second_job_id = "close-collection-batches"
    first_entered = Event()
    release_first = Event()
    second_called = Event()

    def blocking_first_job() -> None:
        first_entered.set()
        assert release_first.wait(timeout=10)

    def second_job() -> None:
        second_called.set()

    monkeypatch.setattr(
        scheduler_jobs,
        "registered_job_functions",
        lambda: {
            first_job_id: blocking_first_job,
            second_job_id: second_job,
        },
    )
    first_pending_id = uuid4()
    second_pending_id = uuid4()
    now = datetime.now(UTC)
    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            (
                ScheduledJobExecution(
                    execution_id=first_pending_id,
                    job_id=first_job_id,
                    trigger_type="MANUAL",
                    status="PENDING",
                    scheduled_at=now - timedelta(minutes=1),
                    created_at=now - timedelta(minutes=1),
                ),
                ScheduledJobExecution(
                    execution_id=second_pending_id,
                    job_id=second_job_id,
                    trigger_type="MANUAL",
                    status="PENDING",
                    scheduled_at=now,
                    created_at=now,
                ),
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_dispatch = executor.submit(dispatch_manual_scheduled_job)
        assert first_entered.wait(timeout=10)
        second_dispatch = executor.submit(dispatch_manual_scheduled_job)
        try:
            assert second_called.wait(timeout=10)
            second_dispatch.result(timeout=10)
        finally:
            release_first.set()
        first_dispatch.result(timeout=10)

    with SyncSessionFactory() as session:
        first_execution = session.get(ScheduledJobExecution, first_pending_id)
        second_execution = session.get(ScheduledJobExecution, second_pending_id)
    assert first_execution is not None
    assert first_execution.status == "SUCCESS"
    assert second_execution is not None
    assert second_execution.status == "SUCCESS"

    with SyncSessionFactory() as session, session.begin():
        executions = tuple(
            session.scalars(
                select(ScheduledJobExecution).where(
                    ScheduledJobExecution.job_id.in_((first_job_id, second_job_id))
                )
            )
        )
        for execution in executions:
            session.delete(execution)


def test_manual_dispatch_recovers_unlocked_running_row_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = "plan-processing-tasks"
    stale_running_id = uuid4()
    pending_id = uuid4()
    called = Event()
    now = datetime.now(UTC)
    monkeypatch.setattr(
        scheduler_jobs,
        "registered_job_functions",
        lambda: {job_id: called.set},
    )
    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            (
                ScheduledJobExecution(
                    execution_id=stale_running_id,
                    job_id=job_id,
                    trigger_type="SCHEDULED",
                    status="RUNNING",
                    scheduled_at=now - timedelta(minutes=2),
                    started_at=now - timedelta(minutes=2),
                    created_at=now - timedelta(minutes=2),
                ),
                ScheduledJobExecution(
                    execution_id=pending_id,
                    job_id=job_id,
                    trigger_type="MANUAL",
                    status="PENDING",
                    scheduled_at=now,
                    created_at=now,
                ),
            )
        )

    dispatch_manual_scheduled_job()

    with SyncSessionFactory() as session:
        stale_running = session.get(ScheduledJobExecution, stale_running_id)
        pending = session.get(ScheduledJobExecution, pending_id)
    assert called.is_set()
    assert stale_running is not None
    assert stale_running.status == "FAILED"
    assert stale_running.finished_at is not None
    assert pending is not None
    assert pending.status == "SUCCESS"

    with SyncSessionFactory() as session, session.begin():
        for execution_id in (stale_running_id, pending_id):
            execution = session.get(ScheduledJobExecution, execution_id)
            if execution is not None:
                session.delete(execution)
