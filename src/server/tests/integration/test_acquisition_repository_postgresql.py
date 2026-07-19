import os
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.common.errors import BatchPlanningError
from app.db.sync_session import SyncSessionFactory
from app.modules.acquisition.domain import TaskBlueprint
from app.modules.acquisition.models import BatchType, CollectionTaskStatus
from app.modules.acquisition.repository import AcquisitionRepository
from app.storage import RawAssetMetadata

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)

TIMEZONE = ZoneInfo("Asia/Shanghai")


def test_collection_repository_state_machine_roundtrip() -> None:
    repository = AcquisitionRepository(SyncSessionFactory)
    business_date = date(2026, 7, 17)
    scheduled_at = datetime(2026, 7, 17, 8, 45, tzinfo=TIMEZONE)
    now = datetime(2026, 7, 17, 9, 0, tzinfo=TIMEZONE)
    blueprints = (
        TaskBlueprint("TUSHARE", "daily", "part=1", {"part": 1}, 3),
        TaskBlueprint("TUSHARE", "daily", "part=2", {"part": 2}, 1),
    )

    batch_id = repository.create_or_get_batch(
        batch_type=BatchType.DAILY,
        business_date=business_date,
        scheduled_at=scheduled_at,
    )
    first_plan = repository.append_tasks(batch_id, blueprints, finalize=True, now=now)
    repeated_plan = repository.append_tasks(batch_id, blueprints, finalize=True, now=now)

    assert first_plan.created_task_count == 2
    assert repeated_plan.created_task_count == 0
    assert repeated_plan.plan_version == first_plan.plan_version

    first_task = repository.claim_next(allowed_batch_types=set(BatchType), now=now)
    assert first_task is not None and first_task.attempt_count == 1
    retry_at = now + timedelta(minutes=1)
    transition = repository.fail_task(
        first_task.task_id,
        error_code="NETWORK_ERROR",
        error_message="temporary",
        request_count=1,
        retry_at=retry_at,
        completed_at=now,
    )
    assert transition.status == CollectionTaskStatus.RETRY_WAIT
    remaining_task = repository.claim_next(allowed_batch_types=set(BatchType), now=now)
    assert remaining_task is not None
    repository.fail_task(
        remaining_task.task_id,
        error_code="UNSUPPORTED",
        error_message="unsupported scope",
        request_count=1,
        retry_at=None,
        completed_at=now,
        skipped=True,
    )

    retried_task = repository.claim_next(
        allowed_batch_types=set(BatchType),
        now=retry_at,
    )
    assert retried_task is not None and retried_task.task_id == first_task.task_id
    assert retried_task.attempt_count == 2
    repository.complete_task(
        retried_task,
        RawAssetMetadata(
            storage_uri=f"file:///tmp/{uuid4()}.parquet",
            content_hash="a" * 64,
            schema_fingerprint="b" * 64,
            row_count=10,
            size_bytes=100,
        ),
        request_count=1,
        empty=False,
        completed_at=retry_at,
    )

    assert repository.close_ready_batches(now=retry_at) == (batch_id,)
    with pytest.raises(BatchPlanningError, match="cannot accept tasks"):
        repository.append_tasks(
            batch_id,
            (TaskBlueprint("TUSHARE", "daily", "part=3", {"part": 3}, 1),),
            finalize=True,
            now=retry_at,
        )
