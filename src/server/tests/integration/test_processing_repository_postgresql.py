import os
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.catalog import (
    DatasetDependencySpec,
    DatasetSpec,
    DependencyKind,
    QualityRuleSpec,
    ReleaseScope,
    WriteStrategy,
)
from app.db.sync_session import SyncSessionFactory
from app.modules.acquisition.domain import TaskBlueprint
from app.modules.acquisition.models import BatchType
from app.modules.acquisition.repository import AcquisitionRepository
from app.modules.processing.models import DatasetRelease
from app.modules.processing.processors.base import PreparedDataset, PublicationResult
from app.modules.processing.repository import ProcessingRepository
from app.storage import RawAssetMetadata

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)

TIMEZONE = ZoneInfo("Asia/Shanghai")


class NoopProcessor:
    name = "noop"

    def prepare(self, *_args: object, **_kwargs: object) -> PreparedDataset:
        return PreparedDataset(None, 1)

    def write(
        self,
        session: Session,
        prepared: PreparedDataset,
        *,
        published_at: datetime,
    ) -> PublicationResult:
        del session, prepared, published_at
        return PublicationResult(1)


class FailingProcessor(NoopProcessor):
    def write(
        self,
        session: Session,
        prepared: PreparedDataset,
        *,
        published_at: datetime,
    ) -> PublicationResult:
        del session, prepared, published_at
        raise RuntimeError("publication failed")


def test_processing_dependencies_and_global_slot_roundtrip() -> None:
    now = datetime(2026, 7, 18, 9, tzinfo=TIMEZONE)
    business_date = date(2026, 7, 18)
    acquisition = AcquisitionRepository(SyncSessionFactory)
    batch_id = acquisition.create_or_get_batch(
        batch_type=BatchType.REPAIR,
        business_date=business_date,
        scheduled_at=now,
    )
    acquisition.append_tasks(
        batch_id,
        (
            TaskBlueprint("TUSHARE", "raw_upstream", "date", {}, 1),
            TaskBlueprint("TUSHARE", "raw_downstream", "date", {}, 1),
        ),
        finalize=True,
        now=now,
    )
    for index in range(2):
        task = acquisition.claim_next(allowed_batch_types={BatchType.REPAIR}, now=now)
        assert task is not None
        acquisition.complete_task(
            task,
            RawAssetMetadata(
                storage_uri=f"file:///tmp/{uuid4()}.parquet",
                content_hash=f"{index + 1}" * 64,
                schema_fingerprint="a" * 64,
                row_count=1,
                size_bytes=1,
            ),
            request_count=1,
            empty=False,
            completed_at=now,
        )
    assert acquisition.close_ready_batches(now=now) == (batch_id,)

    upstream = _dataset("upstream", "raw_upstream")
    downstream = _dataset(
        "downstream",
        "raw_downstream",
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "upstream",
            ReleaseScope.DATE,
        ),
    )
    repository = ProcessingRepository(SyncSessionFactory)
    first_plan = repository.plan_closed_batches((downstream, upstream), now=now)
    repeated_plan = repository.plan_closed_batches((downstream, upstream), now=now)
    assert first_plan.created_task_count == 2
    assert repeated_plan.created_task_count == 0

    first = repository.claim_next(now=now, advisory_lock_id=731_599_901)
    assert first is not None and first.output_dataset == "upstream"
    repository.publish_success(
        first,
        upstream,
        prepared=PreparedDataset(None, 1),
        processor=NoopProcessor(),
        published_at=now,
        rows_read=1,
        rows_rejected=0,
    )

    second = repository.claim_next(now=now, advisory_lock_id=731_599_901)
    assert second is not None and second.output_dataset == "downstream"
    assert repository.claim_next(now=now, advisory_lock_id=731_599_901) is None

    repository.publish_success(
        second,
        downstream,
        prepared=PreparedDataset(None, 1),
        processor=NoopProcessor(),
        published_at=now,
        rows_read=1,
        rows_rejected=0,
    )
    with SyncSessionFactory() as session:
        baseline_downstream_release = session.get(
            DatasetRelease,
            ("downstream", "DATE", business_date.isoformat()),
        )
        assert baseline_downstream_release is not None
        baseline_downstream_process_id = baseline_downstream_release.process_id

    repair_time = now + timedelta(hours=1)
    repair_batch_id = acquisition.create_or_get_batch(
        batch_type=BatchType.REPAIR,
        business_date=business_date,
        scheduled_at=repair_time,
    )
    acquisition.append_tasks(
        repair_batch_id,
        (TaskBlueprint("TUSHARE", "raw_upstream", "date", {}, 1),),
        finalize=True,
        now=repair_time,
    )
    repair_collection = acquisition.claim_next(
        allowed_batch_types={BatchType.REPAIR},
        now=repair_time,
    )
    assert repair_collection is not None
    acquisition.complete_task(
        repair_collection,
        RawAssetMetadata(
            storage_uri=f"file:///tmp/{uuid4()}.parquet",
            content_hash="f" * 64,
            schema_fingerprint="a" * 64,
            row_count=1,
            size_bytes=1,
        ),
        request_count=1,
        empty=False,
        completed_at=repair_time,
    )
    assert acquisition.close_ready_batches(now=repair_time) == (repair_batch_id,)

    repair_plan = repository.plan_closed_batches((downstream, upstream), now=repair_time)
    assert repair_plan.created_task_count == 2
    repaired_upstream = repository.claim_next(
        now=repair_time,
        advisory_lock_id=731_599_901,
    )
    assert repaired_upstream is not None
    assert repaired_upstream.output_dataset == "upstream"
    repository.publish_success(
        repaired_upstream,
        upstream,
        prepared=PreparedDataset(None, 1),
        processor=NoopProcessor(),
        published_at=repair_time,
        rows_read=1,
        rows_rejected=0,
    )
    repaired_downstream = repository.claim_next(
        now=repair_time,
        advisory_lock_id=731_599_901,
    )
    assert repaired_downstream is not None
    assert repaired_downstream.output_dataset == "downstream"
    repaired_raw_names = {
        item.dependency_name for item in repository.raw_dependencies(repaired_downstream.process_id)
    }
    assert repaired_raw_names == {"raw_downstream"}

    with pytest.raises(RuntimeError, match="publication failed"):
        repository.publish_success(
            repaired_downstream,
            downstream,
            prepared=PreparedDataset(None, 1),
            processor=FailingProcessor(),
            published_at=now,
            rows_read=1,
            rows_rejected=0,
        )
    repository.fail_task(
        repaired_downstream,
        message="publication failed",
        retryable=False,
        now=now,
    )

    with SyncSessionFactory() as session:
        current_downstream_release = session.get(
            DatasetRelease,
            ("downstream", "DATE", business_date.isoformat()),
        )
    assert current_downstream_release is not None
    assert current_downstream_release.process_id == baseline_downstream_process_id


def _dataset(
    name: str,
    raw_name: str,
    *extra_dependencies: DatasetDependencySpec,
) -> DatasetSpec:
    return DatasetSpec(
        dataset_name=name,
        processor="noop",
        processor_version="1",
        dependencies=(
            DatasetDependencySpec(
                DependencyKind.RAW_ASSET,
                raw_name,
                ReleaseScope.DATE,
            ),
            *extra_dependencies,
        ),
        write_strategy=WriteStrategy.UPSERT_KEY,
        release_scope=ReleaseScope.DATE,
        quality_rules=(QualityRuleSpec("test"),),
    )
