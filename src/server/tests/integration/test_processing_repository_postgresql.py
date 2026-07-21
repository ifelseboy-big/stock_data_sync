import os
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import update
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
from app.modules.acquisition.models import BatchStatus, BatchType, CollectionBatch
from app.modules.acquisition.repository import AcquisitionRepository
from app.modules.processing.models import (
    DatasetRelease,
    DependencyStatus,
    DependencyType,
    ProcessingDependency,
    ProcessingTask,
    ProcessingTaskStatus,
)
from app.modules.processing.processors.base import PreparedDataset, PublicationResult
from app.modules.processing.repository import ProcessingRepository
from app.modules.stocks.models import Stock
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


def test_processing_claim_respects_worker_limit_and_dataset_mutex() -> None:
    now = datetime(2037, 1, 20, 8, tzinfo=TIMEZONE)
    shared_first_id = uuid4()
    shared_second_id = uuid4()
    independent_id = uuid4()
    batches = tuple(uuid4() for _ in range(3))
    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            CollectionBatch(
                batch_id=batch_id,
                batch_type=BatchType.BACKFILL.value,
                business_date=date(2037, 1, 20 + index),
                status=BatchStatus.CLOSED.value,
                scheduled_at=now + timedelta(seconds=index),
                closed_at=now,
            )
            for index, batch_id in enumerate(batches)
        )
        session.add_all(
            (
                _queued_process(shared_first_id, batches[0], "shared", now, priority=100),
                _queued_process(shared_second_id, batches[1], "shared", now, priority=101),
                _queued_process(independent_id, batches[2], "independent", now, priority=102),
            )
        )

    repository = ProcessingRepository(SyncSessionFactory)
    first = repository.claim_next(
        now=now,
        advisory_lock_id=731_599_903,
        max_running_tasks=3,
    )
    second = repository.claim_next(
        now=now,
        advisory_lock_id=731_599_903,
        max_running_tasks=3,
    )
    third = repository.claim_next(
        now=now,
        advisory_lock_id=731_599_903,
        max_running_tasks=3,
    )

    assert first is not None and first.process_id == shared_first_id
    assert second is not None and second.process_id == independent_id
    assert third is None

    with SyncSessionFactory() as session, session.begin():
        session.execute(
            update(ProcessingTask)
            .where(ProcessingTask.process_id == shared_first_id)
            .values(status=ProcessingTaskStatus.SUCCESS.value, finished_at=now)
        )
    shared_second = repository.claim_next(
        now=now,
        advisory_lock_id=731_599_903,
        max_running_tasks=3,
    )
    assert shared_second is not None and shared_second.process_id == shared_second_id

    with SyncSessionFactory() as session, session.begin():
        session.execute(
            update(ProcessingTask)
            .where(ProcessingTask.process_id.in_((independent_id, shared_second_id)))
            .values(status=ProcessingTaskStatus.SUCCESS.value, finished_at=now)
        )


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


def test_stock_release_requeues_failures_after_unknown_codes_become_available() -> None:
    now = datetime(2037, 2, 2, 19, 5, tzinfo=TIMEZONE)
    old_stock_process_id = uuid4()
    new_stock_process_id = uuid4()
    failed_process_id = uuid4()
    master_batch_id = uuid4()
    daily_batch_id = uuid4()
    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            (
                CollectionBatch(
                    batch_id=master_batch_id,
                    batch_type=BatchType.DAILY.value,
                    business_date=now.date(),
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now,
                    closed_at=now,
                ),
                CollectionBatch(
                    batch_id=daily_batch_id,
                    batch_type=BatchType.DAILY.value,
                    business_date=now.date(),
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(hours=3),
                    closed_at=now - timedelta(hours=1),
                ),
            )
        )
        session.add_all(
            (
                ProcessingTask(
                    process_id=old_stock_process_id,
                    source_batch_id=daily_batch_id,
                    process_type="stock@1",
                    business_date=now.date(),
                    output_dataset="stock",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.SUCCESS.value,
                    priority=100,
                    attempt_count=1,
                    max_attempts=2,
                    started_at=now - timedelta(hours=4),
                    finished_at=now - timedelta(hours=4),
                ),
                ProcessingTask(
                    process_id=new_stock_process_id,
                    source_batch_id=master_batch_id,
                    process_type="stock@1",
                    business_date=now.date(),
                    output_dataset="stock",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.QUEUED.value,
                    priority=100,
                    attempt_count=0,
                    max_attempts=2,
                    queued_at=now,
                ),
                ProcessingTask(
                    process_id=failed_process_id,
                    source_batch_id=daily_batch_id,
                    process_type="stock_moneyflow_daily@1",
                    business_date=now.date(),
                    output_dataset="stock_moneyflow_daily",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.FAILED.value,
                    priority=100,
                    attempt_count=2,
                    max_attempts=2,
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=1),
                    error_message="dataset references unknown stocks: ['699999.SH']",
                ),
                Stock(
                    ts_code="699999.SH",
                    symbol="699999",
                    name="自动恢复测试",
                    exchange="SSE",
                    list_status="L",
                    synced_at=now,
                ),
            )
        )
        session.flush()
        session.add(
            ProcessingDependency(
                process_id=failed_process_id,
                dependency_type=DependencyType.DATASET_RELEASE.value,
                dependency_name="stock",
                dependency_scope_key="GLOBAL",
                dependency_scope={"scope_type": "GLOBAL", "scope_key": "GLOBAL"},
                status=DependencyStatus.READY.value,
                resolved_release_process_id=old_stock_process_id,
            )
        )

    stock_spec = DatasetSpec(
        dataset_name="stock",
        processor="noop",
        processor_version="1",
        dependencies=(
            DatasetDependencySpec(DependencyKind.RAW_ASSET, "stock_basic", ReleaseScope.GLOBAL),
        ),
        write_strategy=WriteStrategy.MASTER_MERGE,
        release_scope=ReleaseScope.GLOBAL,
        quality_rules=(QualityRuleSpec("test"),),
    )
    repository = ProcessingRepository(SyncSessionFactory)
    claimed = repository.claim_next(now=now, advisory_lock_id=731_599_904)
    assert claimed is not None and claimed.process_id == new_stock_process_id

    repository.publish_success(
        claimed,
        stock_spec,
        prepared=PreparedDataset(None, 1),
        processor=NoopProcessor(),
        published_at=now,
        rows_read=1,
        rows_rejected=0,
    )

    with SyncSessionFactory() as session:
        recovered = session.get(ProcessingTask, failed_process_id)
        dependency = session.get(
            ProcessingDependency,
            (failed_process_id, DependencyType.DATASET_RELEASE.value, "stock", "GLOBAL"),
        )
    assert recovered is not None
    assert recovered.status == ProcessingTaskStatus.QUEUED.value
    assert recovered.max_attempts == 3
    assert recovered.error_message is None
    assert dependency is not None
    assert dependency.status == DependencyStatus.READY.value
    assert dependency.resolved_release_process_id == new_stock_process_id


def test_unknown_stock_reconciliation_requests_only_one_newer_master_refresh() -> None:
    now = datetime(2037, 2, 3, 19, 5, tzinfo=TIMEZONE)
    stock_process_id = uuid4()
    failed_process_id = uuid4()
    master_batch_id = uuid4()
    daily_batch_id = uuid4()
    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            (
                CollectionBatch(
                    batch_id=master_batch_id,
                    batch_type=BatchType.MASTER.value,
                    business_date=now.date(),
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(hours=3),
                    closed_at=now - timedelta(hours=2),
                ),
                CollectionBatch(
                    batch_id=daily_batch_id,
                    batch_type=BatchType.DAILY.value,
                    business_date=now.date(),
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(hours=2),
                    closed_at=now - timedelta(minutes=30),
                ),
            )
        )
        session.add_all(
            (
                ProcessingTask(
                    process_id=stock_process_id,
                    source_batch_id=master_batch_id,
                    process_type="stock@1",
                    business_date=now.date(),
                    output_dataset="stock",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.SUCCESS.value,
                    priority=100,
                    attempt_count=1,
                    max_attempts=2,
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=2),
                ),
                ProcessingTask(
                    process_id=failed_process_id,
                    source_batch_id=daily_batch_id,
                    process_type="stock_daily_core@1",
                    business_date=now.date(),
                    output_dataset="stock_daily.core",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.FAILED.value,
                    priority=100,
                    attempt_count=2,
                    max_attempts=2,
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=5),
                    error_message="dataset references unknown stocks: ['699998.SH']",
                ),
            )
        )
        session.flush()
        release = session.get(DatasetRelease, ("stock", "GLOBAL", "GLOBAL"))
        if release is None:
            session.add(
                DatasetRelease(
                    dataset_name="stock",
                    scope_type="GLOBAL",
                    scope_key="GLOBAL",
                    business_date=None,
                    version_id=uuid4(),
                    process_id=stock_process_id,
                    row_count=1,
                    published_at=now - timedelta(hours=2),
                )
            )
        else:
            release.version_id = uuid4()
            release.process_id = stock_process_id
            release.row_count = 1
            release.published_at = now - timedelta(hours=2)
        session.add(
            ProcessingDependency(
                process_id=failed_process_id,
                dependency_type=DependencyType.DATASET_RELEASE.value,
                dependency_name="stock",
                dependency_scope_key="GLOBAL",
                dependency_scope={"scope_type": "GLOBAL", "scope_key": "GLOBAL"},
                status=DependencyStatus.READY.value,
                resolved_release_process_id=stock_process_id,
            )
        )

    repository = ProcessingRepository(SyncSessionFactory)
    first = repository.reconcile_unknown_stock_failures(now=now)

    assert first.requeued_count == 0
    assert first.missing_codes == ("699998.SH",)
    assert first.master_refresh_required is True
    assert first.latest_failure_at == now - timedelta(minutes=5)

    with SyncSessionFactory() as session, session.begin():
        release = session.get(DatasetRelease, ("stock", "GLOBAL", "GLOBAL"))
        assert release is not None
        release.published_at = now + timedelta(minutes=1)

    after_newer_refresh = repository.reconcile_unknown_stock_failures(
        now=now + timedelta(minutes=2)
    )

    assert after_newer_refresh.missing_codes == ("699998.SH",)
    assert after_newer_refresh.master_refresh_required is False


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


def _queued_process(
    process_id: UUID,
    batch_id: UUID,
    dataset: str,
    now: datetime,
    *,
    priority: int,
) -> ProcessingTask:
    return ProcessingTask(
        process_id=process_id,
        source_batch_id=batch_id,
        process_type="noop@1",
        business_date=now.date(),
        output_dataset=dataset,
        output_version=uuid4(),
        status=ProcessingTaskStatus.QUEUED.value,
        priority=priority,
        queued_at=now,
    )
