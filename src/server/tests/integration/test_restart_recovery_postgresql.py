import os
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.db.sync_session import SyncSessionFactory
from app.modules.acquisition.models import BatchStatus, BatchType, CollectionBatch
from app.modules.processing.models import ProcessingTask, ProcessingTaskStatus
from app.modules.processing.repository import ProcessingRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)

TIMEZONE = ZoneInfo("Asia/Shanghai")


def test_interrupted_final_processing_attempt_is_retried_without_spending_an_attempt() -> None:
    now = datetime(2037, 1, 24, 9, tzinfo=TIMEZONE)
    batch_id = uuid4()
    process_id = uuid4()
    with SyncSessionFactory() as session, session.begin():
        session.add(
            CollectionBatch(
                batch_id=batch_id,
                batch_type=BatchType.REPAIR.value,
                business_date=now.date(),
                status=BatchStatus.CLOSED.value,
                scheduled_at=now,
                closed_at=now,
            )
        )
        session.add(
            ProcessingTask(
                process_id=process_id,
                source_batch_id=batch_id,
                process_type="noop@1",
                business_date=now.date(),
                output_dataset="restart_recovery",
                output_version=uuid4(),
                status=ProcessingTaskStatus.QUEUED.value,
                priority=50,
                attempt_count=0,
                max_attempts=1,
                queued_at=now,
            )
        )

    repository = ProcessingRepository(SyncSessionFactory)
    interrupted = repository.claim_next(now=now, advisory_lock_id=731_599_902)
    assert interrupted is not None
    assert interrupted.process_id == process_id
    assert interrupted.attempt_count == interrupted.max_attempts == 1

    assert repository.recover_running_tasks(now=now) == 1

    resumed = repository.claim_next(now=now, advisory_lock_id=731_599_902)
    assert resumed is not None
    assert resumed.process_id == process_id
    assert resumed.attempt_count == resumed.max_attempts == 1

    terminal = repository.fail_task(
        resumed,
        message="real failure still spends the final attempt",
        retryable=True,
        now=now,
    )
    assert terminal.status == ProcessingTaskStatus.FAILED
