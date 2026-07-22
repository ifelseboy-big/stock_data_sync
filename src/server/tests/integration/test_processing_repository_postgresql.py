import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.catalog import (
    DatasetDependencySpec,
    DatasetSpec,
    DependencyKind,
    QualityRuleSpec,
    ReleaseScope,
    WriteStrategy,
)
from app.common.errors import ProcessingError
from app.db.sync_session import SyncSessionFactory
from app.modules.acquisition.domain import TaskBlueprint
from app.modules.acquisition.models import (
    BatchStatus,
    BatchType,
    CollectionBatch,
    CollectionTask,
    CollectionTaskStatus,
    RawDataAsset,
)
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
from app.modules.processing.repository import (
    PROCESSING_VERSION_NAMESPACE,
    ProcessingRepository,
    _lock_publication_scope,
    _processing_output_version,
)
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


def test_stock_daily_limit_waits_for_active_core_on_same_date() -> None:
    now = datetime(2037, 1, 21, 8, tzinfo=TIMEZONE)
    core_batch_id = uuid4()
    limit_batch_id = uuid4()
    stale_core_batch_id = uuid4()
    core_process_id = uuid4()
    limit_process_id = uuid4()
    stale_core_process_id = uuid4()
    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            (
                CollectionBatch(
                    batch_id=core_batch_id,
                    batch_type=BatchType.BACKFILL.value,
                    business_date=now.date(),
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now,
                ),
                CollectionBatch(
                    batch_id=limit_batch_id,
                    batch_type=BatchType.BACKFILL.value,
                    business_date=now.date(),
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now + timedelta(seconds=1),
                ),
                CollectionBatch(
                    batch_id=stale_core_batch_id,
                    batch_type=BatchType.BACKFILL.value,
                    business_date=now.date(),
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(days=1),
                ),
                _queued_process(
                    limit_process_id,
                    limit_batch_id,
                    "stock_daily.limit",
                    now - timedelta(minutes=1),
                    priority=400,
                ),
                _queued_process(
                    core_process_id,
                    core_batch_id,
                    "stock_daily.core",
                    now,
                    priority=400,
                ),
                ProcessingTask(
                    process_id=stale_core_process_id,
                    source_batch_id=stale_core_batch_id,
                    process_type="stock_daily_core@2",
                    business_date=now.date(),
                    output_dataset="stock_daily.core",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.WAITING_DEPENDENCY.value,
                    priority=400,
                ),
            )
        )

    repository = ProcessingRepository(SyncSessionFactory)
    claimed_core = repository.claim_next(
        now=now,
        advisory_lock_id=731_599_905,
        max_running_tasks=2,
        source_batch_ids=(core_batch_id, limit_batch_id),
    )
    assert claimed_core is not None
    assert claimed_core.process_id == core_process_id
    with SyncSessionFactory() as session:
        with pytest.raises(ProcessingError, match="waiting for the latest"):
            repository._ensure_no_active_stock_daily_core(
                session,
                business_date=now.date(),
            )

    with SyncSessionFactory() as session, session.begin():
        core = session.get(ProcessingTask, core_process_id)
        assert core is not None
        core.status = ProcessingTaskStatus.SUCCESS.value
        core.finished_at = now

    claimed_limit = repository.claim_next(
        now=now,
        advisory_lock_id=731_599_905,
        max_running_tasks=2,
        source_batch_ids=(core_batch_id, limit_batch_id),
    )
    assert claimed_limit is not None
    assert claimed_limit.process_id == limit_process_id
    with SyncSessionFactory() as session, session.begin():
        limit = session.get(ProcessingTask, limit_process_id)
        assert limit is not None
        limit.status = ProcessingTaskStatus.SUCCESS.value
        limit.finished_at = now


@pytest.mark.parametrize("limit_path", ("fallback", "planned", "consecutive"))
def test_new_stock_daily_core_invalidates_release_and_uses_current_limit_task(
    limit_path: str,
) -> None:
    path_index = {"fallback": 0, "planned": 1, "consecutive": 2}[limit_path]
    has_planned_limit = limit_path == "planned"
    now = datetime(2037, 1, 25 + path_index, 8, tzinfo=TIMEZONE)
    business_date = now.date()
    old_core_batch_id = uuid4()
    new_core_batch_id = uuid4()
    limit_batch_id = uuid4()
    old_core_process_id = uuid4()
    new_core_process_id = uuid4()
    limit_process_id = uuid4()
    old_core_version = uuid4()
    new_core_version = uuid4()
    limit_version = uuid4()
    newer_limit_batch_id = uuid4()
    newer_limit_process_id = uuid4()
    old_limit_asset_id = uuid4()
    newer_limit_asset_id = uuid4()
    planned_limit_version = _processing_output_version(
        source_batch_id=new_core_batch_id,
        dataset_name="stock_daily.limit",
        processor_version="2",
        business_date=business_date,
    )
    planned_limit_process_id = uuid5(
        PROCESSING_VERSION_NAMESPACE,
        f"process:{planned_limit_version}",
    )
    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            (
                CollectionBatch(
                    batch_id=old_core_batch_id,
                    batch_type=BatchType.BACKFILL.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(hours=2),
                ),
                CollectionBatch(
                    batch_id=limit_batch_id,
                    batch_type=BatchType.BACKFILL.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(hours=1),
                ),
                CollectionBatch(
                    batch_id=new_core_batch_id,
                    batch_type=BatchType.REPAIR.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now,
                ),
                ProcessingTask(
                    process_id=old_core_process_id,
                    source_batch_id=old_core_batch_id,
                    process_type="noop@1",
                    business_date=business_date,
                    output_dataset="stock_daily.core",
                    output_version=old_core_version,
                    status=ProcessingTaskStatus.SUCCESS.value,
                    priority=400,
                    attempt_count=1,
                    queued_at=now - timedelta(hours=2),
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2),
                ),
                ProcessingTask(
                    process_id=limit_process_id,
                    source_batch_id=limit_batch_id,
                    process_type="noop@1",
                    business_date=business_date,
                    output_dataset="stock_daily.limit",
                    output_version=limit_version,
                    status=ProcessingTaskStatus.SUCCESS.value,
                    priority=400,
                    attempt_count=1,
                    queued_at=now - timedelta(hours=1),
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1),
                ),
                ProcessingTask(
                    process_id=new_core_process_id,
                    source_batch_id=new_core_batch_id,
                    process_type="noop@1",
                    business_date=business_date,
                    output_dataset="stock_daily.core",
                    output_version=new_core_version,
                    status=ProcessingTaskStatus.QUEUED.value,
                    priority=200,
                    queued_at=now,
                ),
            )
        )
        if has_planned_limit:
            session.add(
                ProcessingTask(
                    process_id=planned_limit_process_id,
                    source_batch_id=new_core_batch_id,
                    process_type="stock_daily_limit@2",
                    business_date=business_date,
                    output_dataset="stock_daily.limit",
                    output_version=planned_limit_version,
                    status=ProcessingTaskStatus.WAITING_DEPENDENCY.value,
                    priority=200,
                )
            )
        if limit_path == "consecutive":
            old_limit_collection_task_id = uuid4()
            newer_limit_collection_task_id = uuid4()
            session.add_all(
                (
                    CollectionBatch(
                        batch_id=newer_limit_batch_id,
                        batch_type=BatchType.BACKFILL.value,
                        business_date=business_date,
                        status=BatchStatus.CLOSED.value,
                        scheduled_at=now - timedelta(minutes=30),
                        closed_at=now - timedelta(minutes=20),
                    ),
                    CollectionTask(
                        task_id=old_limit_collection_task_id,
                        batch_id=limit_batch_id,
                        provider="TUSHARE",
                        api_name="stk_limit",
                        scope_key="old",
                        request_params={},
                        status=CollectionTaskStatus.SUCCESS.value,
                        max_attempts=3,
                        finished_at=now - timedelta(hours=1),
                    ),
                    CollectionTask(
                        task_id=newer_limit_collection_task_id,
                        batch_id=newer_limit_batch_id,
                        provider="TUSHARE",
                        api_name="stk_limit",
                        scope_key="new",
                        request_params={},
                        status=CollectionTaskStatus.SUCCESS.value,
                        max_attempts=3,
                        finished_at=now - timedelta(minutes=20),
                    ),
                    ProcessingTask(
                        process_id=newer_limit_process_id,
                        source_batch_id=newer_limit_batch_id,
                        process_type="stock_daily_limit@2",
                        business_date=business_date,
                        output_dataset="stock_daily.limit",
                        output_version=uuid4(),
                        status=ProcessingTaskStatus.QUEUED.value,
                        priority=400,
                        queued_at=now - timedelta(minutes=20),
                    ),
                )
            )
            session.flush()
            session.add_all(
                (
                    RawDataAsset(
                        asset_id=old_limit_asset_id,
                        task_id=old_limit_collection_task_id,
                        provider="TUSHARE",
                        api_name="stk_limit",
                        business_date=business_date,
                        request_params={},
                        storage_uri="file:///tmp/old-stk-limit.parquet",
                        content_hash="a" * 64,
                        schema_fingerprint="b" * 64,
                        row_count=1,
                        is_complete=True,
                        fetched_at=now - timedelta(hours=1),
                        sealed_at=now - timedelta(hours=1),
                    ),
                    RawDataAsset(
                        asset_id=newer_limit_asset_id,
                        task_id=newer_limit_collection_task_id,
                        provider="TUSHARE",
                        api_name="stk_limit",
                        business_date=business_date,
                        request_params={},
                        storage_uri="file:///tmp/new-stk-limit.parquet",
                        content_hash="c" * 64,
                        schema_fingerprint="d" * 64,
                        row_count=1,
                        is_complete=True,
                        fetched_at=now - timedelta(minutes=20),
                        sealed_at=now - timedelta(minutes=20),
                    ),
                )
            )
        session.flush()
        session.add_all(
            (
                DatasetRelease(
                    dataset_name="stock_daily.core",
                    scope_type=ReleaseScope.DATE.value,
                    scope_key=business_date.isoformat(),
                    business_date=business_date,
                    version_id=old_core_version,
                    process_id=old_core_process_id,
                    row_count=1,
                    published_at=now - timedelta(hours=2),
                ),
                DatasetRelease(
                    dataset_name="stock_daily.limit",
                    scope_type=ReleaseScope.DATE.value,
                    scope_key=business_date.isoformat(),
                    business_date=business_date,
                    version_id=limit_version,
                    process_id=limit_process_id,
                    row_count=1,
                    published_at=now - timedelta(hours=1),
                ),
                ProcessingDependency(
                    process_id=limit_process_id,
                    dependency_type=DependencyType.DATASET_RELEASE.value,
                    dependency_name="stock_daily.core",
                    dependency_scope_key=business_date.isoformat(),
                    dependency_scope={
                        "scope_type": ReleaseScope.DATE.value,
                        "scope_key": business_date.isoformat(),
                    },
                    status=DependencyStatus.READY.value,
                    resolved_release_process_id=old_core_process_id,
                ),
            )
        )
        if has_planned_limit:
            session.add(
                ProcessingDependency(
                    process_id=planned_limit_process_id,
                    dependency_type=DependencyType.DATASET_RELEASE.value,
                    dependency_name="stock_daily.core",
                    dependency_scope_key=business_date.isoformat(),
                    dependency_scope={
                        "scope_type": ReleaseScope.DATE.value,
                        "scope_key": business_date.isoformat(),
                    },
                    status=DependencyStatus.WAITING.value,
                    resolved_release_process_id=new_core_process_id,
                )
            )
        if limit_path == "consecutive":
            session.add_all(
                (
                    ProcessingDependency(
                        process_id=limit_process_id,
                        dependency_type=DependencyType.RAW_ASSET.value,
                        dependency_name="stk_limit",
                        dependency_scope_key="old",
                        dependency_scope={},
                        status=DependencyStatus.READY.value,
                        resolved_asset_id=old_limit_asset_id,
                    ),
                    ProcessingDependency(
                        process_id=newer_limit_process_id,
                        dependency_type=DependencyType.RAW_ASSET.value,
                        dependency_name="stk_limit",
                        dependency_scope_key="new",
                        dependency_scope={},
                        status=DependencyStatus.READY.value,
                        resolved_asset_id=newer_limit_asset_id,
                    ),
                    ProcessingDependency(
                        process_id=newer_limit_process_id,
                        dependency_type=DependencyType.DATASET_RELEASE.value,
                        dependency_name="stock_daily.core",
                        dependency_scope_key=business_date.isoformat(),
                        dependency_scope={
                            "scope_type": ReleaseScope.DATE.value,
                            "scope_key": business_date.isoformat(),
                        },
                        status=DependencyStatus.READY.value,
                        resolved_release_process_id=old_core_process_id,
                    ),
                )
            )

    repository = ProcessingRepository(SyncSessionFactory)
    claimed_core = repository.claim_next(
        now=now,
        advisory_lock_id=731_599_906,
        source_batch_ids=(new_core_batch_id,),
    )
    assert claimed_core is not None
    core_spec = DatasetSpec(
        dataset_name="stock_daily.core",
        processor="noop",
        processor_version="1",
        dependencies=(
            DatasetDependencySpec(
                DependencyKind.RAW_ASSET,
                "daily",
                ReleaseScope.DATE,
            ),
        ),
        write_strategy=WriteStrategy.REPLACE_DATE,
        release_scope=ReleaseScope.DATE,
        quality_rules=(QualityRuleSpec("test"),),
    )
    repository.publish_success(
        claimed_core,
        core_spec,
        prepared=PreparedDataset(None, 1),
        processor=NoopProcessor(),
        published_at=now,
        rows_read=1,
        rows_rejected=0,
    )

    with SyncSessionFactory() as session:
        current_core = session.get(
            DatasetRelease,
            ("stock_daily.core", ReleaseScope.DATE.value, business_date.isoformat()),
        )
        stale_limit = session.get(
            DatasetRelease,
            ("stock_daily.limit", ReleaseScope.DATE.value, business_date.isoformat()),
        )
        prior_limit_task = session.get(ProcessingTask, limit_process_id)
        prior_dependency = session.get(
            ProcessingDependency,
            (
                limit_process_id,
                DependencyType.DATASET_RELEASE.value,
                "stock_daily.core",
                business_date.isoformat(),
            ),
        )
        successors = tuple(
            session.scalars(
                select(ProcessingTask).where(
                    ProcessingTask.output_dataset == "stock_daily.limit",
                    ProcessingTask.business_date == business_date,
                    ProcessingTask.process_id != limit_process_id,
                )
            )
        )
        successor = next(
            (
                task
                for task in successors
                if (
                    task.process_id == planned_limit_process_id
                    if has_planned_limit
                    else task.source_batch_id == limit_batch_id
                    and task.process_id != limit_process_id
                )
            ),
            None,
        )
        successor_dependency = (
            None
            if successor is None
            else session.get(
                ProcessingDependency,
                (
                    successor.process_id,
                    DependencyType.DATASET_RELEASE.value,
                    "stock_daily.core",
                    business_date.isoformat(),
                ),
            )
        )
    assert current_core is not None and current_core.process_id == new_core_process_id
    assert stale_limit is None
    assert prior_limit_task is not None
    assert prior_limit_task.status == ProcessingTaskStatus.SUCCESS.value
    assert prior_limit_task.output_version == limit_version
    assert prior_dependency is not None
    assert prior_dependency.resolved_release_process_id == old_core_process_id
    assert successor is not None
    assert len(successors) == (2 if limit_path == "consecutive" else 1)
    assert successor.process_id != limit_process_id
    assert successor.output_version != limit_version
    if has_planned_limit:
        assert successor.process_id == planned_limit_process_id
        assert successor.output_version == planned_limit_version
    else:
        assert successor.process_id != planned_limit_process_id
    assert successor.process_type == "stock_daily_limit@2"
    assert successor.status == ProcessingTaskStatus.QUEUED.value
    assert successor_dependency is not None
    assert successor_dependency.resolved_release_process_id == new_core_process_id
    active_core_process_id = new_core_process_id

    if limit_path == "consecutive":
        second_core_batch_id = uuid4()
        second_core_process_id = uuid4()
        with SyncSessionFactory() as session, session.begin():
            session.add(
                CollectionBatch(
                    batch_id=second_core_batch_id,
                    batch_type=BatchType.REPAIR.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now + timedelta(seconds=1),
                )
            )
            session.add(
                ProcessingTask(
                    process_id=second_core_process_id,
                    source_batch_id=second_core_batch_id,
                    process_type="noop@1",
                    business_date=business_date,
                    output_dataset="stock_daily.core",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.QUEUED.value,
                    priority=200,
                    queued_at=now + timedelta(seconds=1),
                )
            )
        second_claim = repository.claim_next(
            now=now + timedelta(seconds=1),
            advisory_lock_id=731_599_906,
            source_batch_ids=(second_core_batch_id,),
        )
        assert second_claim is not None
        repository.publish_success(
            second_claim,
            core_spec,
            prepared=PreparedDataset(None, 1),
            processor=NoopProcessor(),
            published_at=now + timedelta(seconds=1),
            rows_read=1,
            rows_rejected=0,
        )
        with SyncSessionFactory() as session:
            superseded = session.get(ProcessingTask, successor.process_id)
            newer_raw_task = session.get(ProcessingTask, newer_limit_process_id)
            current_successor = session.scalar(
                select(ProcessingTask).where(
                    ProcessingTask.output_dataset == "stock_daily.limit",
                    ProcessingTask.business_date == business_date,
                    ProcessingTask.process_id.not_in((limit_process_id, successor.process_id)),
                    ProcessingTask.status == ProcessingTaskStatus.QUEUED.value,
                )
            )
            current_dependency = (
                None
                if current_successor is None
                else session.get(
                    ProcessingDependency,
                    (
                        current_successor.process_id,
                        DependencyType.DATASET_RELEASE.value,
                        "stock_daily.core",
                        business_date.isoformat(),
                    ),
                )
            )
            current_raw_dependency = (
                None
                if current_successor is None
                else session.scalar(
                    select(ProcessingDependency).where(
                        ProcessingDependency.process_id == current_successor.process_id,
                        ProcessingDependency.dependency_type == DependencyType.RAW_ASSET.value,
                        ProcessingDependency.dependency_name == "stk_limit",
                    )
                )
            )
        assert superseded is not None
        assert superseded.status == ProcessingTaskStatus.CANCELLED.value
        assert newer_raw_task is not None
        assert newer_raw_task.status == ProcessingTaskStatus.CANCELLED.value
        assert current_successor is not None
        assert current_dependency is not None
        assert current_dependency.resolved_release_process_id == second_core_process_id
        assert current_raw_dependency is not None
        assert current_raw_dependency.resolved_asset_id == newer_limit_asset_id
        successor = current_successor
        active_core_process_id = second_core_process_id

    claimed_limit = repository.claim_next(
        now=now,
        advisory_lock_id=731_599_906,
        source_batch_ids=(limit_batch_id, new_core_batch_id, newer_limit_batch_id),
    )
    assert claimed_limit is not None
    assert claimed_limit.process_id == successor.process_id
    limit_spec = DatasetSpec(
        dataset_name="stock_daily.limit",
        processor="stock_daily_limit",
        processor_version="2",
        dependencies=(
            DatasetDependencySpec(
                DependencyKind.DATASET_RELEASE,
                "stock_daily.core",
                ReleaseScope.DATE,
            ),
        ),
        write_strategy=WriteStrategy.PATCH_COLUMNS,
        release_scope=ReleaseScope.DATE,
        quality_rules=(QualityRuleSpec("test"),),
    )
    repository.publish_success(
        claimed_limit,
        limit_spec,
        prepared=PreparedDataset(None, 1),
        processor=NoopProcessor(),
        published_at=now,
        rows_read=1,
        rows_rejected=0,
    )

    with SyncSessionFactory() as session:
        current_limit = session.get(
            DatasetRelease,
            ("stock_daily.limit", ReleaseScope.DATE.value, business_date.isoformat()),
        )
        refreshed_dependency = session.get(
            ProcessingDependency,
            (
                successor.process_id,
                DependencyType.DATASET_RELEASE.value,
                "stock_daily.core",
                business_date.isoformat(),
            ),
        )
    assert current_limit is not None and current_limit.process_id == successor.process_id
    assert refreshed_dependency is not None
    assert refreshed_dependency.resolved_release_process_id == active_core_process_id

    with SyncSessionFactory() as session, session.begin():
        old_limit = session.get(ProcessingTask, limit_process_id)
        assert old_limit is not None
        old_limit.process_type = "stock_daily_limit@2"
        old_limit.status = ProcessingTaskStatus.QUEUED.value
        old_limit.queued_at = now + timedelta(minutes=1)
    stale_claim = repository.claim_next(
        now=now + timedelta(minutes=1),
        advisory_lock_id=731_599_906,
        source_batch_ids=(limit_batch_id,),
    )
    assert stale_claim is not None and stale_claim.process_id == limit_process_id
    with pytest.raises(ProcessingError, match="output lineage is stale"):
        repository.publish_success(
            stale_claim,
            limit_spec,
            prepared=PreparedDataset(None, 1),
            processor=NoopProcessor(),
            published_at=now + timedelta(minutes=1),
            rows_read=1,
            rows_rejected=0,
        )
    repository.fail_task(
        stale_claim,
        message="output lineage is stale",
        retryable=False,
        now=now + timedelta(minutes=1),
    )
    with SyncSessionFactory() as session:
        preserved_release = session.get(
            DatasetRelease,
            ("stock_daily.limit", ReleaseScope.DATE.value, business_date.isoformat()),
        )
    assert preserved_release is not None
    assert preserved_release.process_id == successor.process_id


def test_processor_upgrade_replans_failed_tasks_without_reprocessing_successes() -> None:
    now = datetime(2037, 1, 23, 8, tzinfo=TIMEZONE)
    acquisition = AcquisitionRepository(SyncSessionFactory)
    batch_id = acquisition.create_or_get_batch(
        batch_type=BatchType.REPAIR,
        business_date=now.date(),
        scheduled_at=now,
    )
    acquisition.append_tasks(
        batch_id,
        (
            TaskBlueprint("TUSHARE", "versioned_upstream_raw", "date", {}, 1),
            TaskBlueprint("TUSHARE", "versioned_downstream_raw", "date", {}, 1),
        ),
        finalize=True,
        now=now,
    )
    for index in range(2):
        collection = acquisition.claim_next(
            allowed_batch_types={BatchType.REPAIR},
            now=now,
        )
        assert collection is not None
        acquisition.complete_task(
            collection,
            RawAssetMetadata(
                storage_uri=f"file:///tmp/versioned-{index}.parquet",
                content_hash=str(index + 1) * 64,
                schema_fingerprint="a" * 64,
                row_count=1,
                size_bytes=1,
            ),
            request_count=1,
            empty=False,
            completed_at=now,
        )
    assert acquisition.close_ready_batches(now=now) == (batch_id,)

    upstream_v1 = _versioned_dataset("versioned_upstream", "versioned_upstream_raw", "1")
    downstream_dependency = DatasetDependencySpec(
        DependencyKind.DATASET_RELEASE,
        "versioned_upstream",
        ReleaseScope.DATE,
    )
    downstream_v1 = _versioned_dataset(
        "versioned_downstream",
        "versioned_downstream_raw",
        "1",
        downstream_dependency,
    )
    stable_v1 = _versioned_dataset("versioned_stable", "versioned_upstream_raw", "1")
    repository = ProcessingRepository(SyncSessionFactory)
    first_plan = repository.plan_closed_batches(
        (downstream_v1, upstream_v1, stable_v1),
        now=now,
        source_batch_ids=(batch_id,),
    )
    assert first_plan.created_task_count == 3

    with SyncSessionFactory() as session, session.begin():
        tasks = tuple(
            session.scalars(
                select(ProcessingTask).where(ProcessingTask.source_batch_id == batch_id)
            )
        )
        for task in tasks:
            task.status = (
                ProcessingTaskStatus.SUCCESS.value
                if task.output_dataset == "versioned_stable"
                else ProcessingTaskStatus.FAILED.value
            )
            task.finished_at = now

    upstream_v2 = _versioned_dataset("versioned_upstream", "versioned_upstream_raw", "2")
    downstream_v2 = _versioned_dataset(
        "versioned_downstream",
        "versioned_downstream_raw",
        "2",
        downstream_dependency,
    )
    stable_v2 = _versioned_dataset("versioned_stable", "versioned_upstream_raw", "2")
    upgrade_plan = repository.plan_closed_batches(
        (downstream_v2, upstream_v2, stable_v2),
        now=now + timedelta(minutes=1),
        source_batch_ids=(batch_id,),
    )
    assert upgrade_plan.created_task_count == 2

    with SyncSessionFactory() as session:
        upgraded_upstream = session.scalar(
            select(ProcessingTask).where(
                ProcessingTask.source_batch_id == batch_id,
                ProcessingTask.output_dataset == "versioned_upstream",
                ProcessingTask.process_type == "noop@2",
            )
        )
        upgraded_downstream = session.scalar(
            select(ProcessingTask).where(
                ProcessingTask.source_batch_id == batch_id,
                ProcessingTask.output_dataset == "versioned_downstream",
                ProcessingTask.process_type == "noop@2",
            )
        )
        stable_v2_task = session.scalar(
            select(ProcessingTask).where(
                ProcessingTask.source_batch_id == batch_id,
                ProcessingTask.output_dataset == "versioned_stable",
                ProcessingTask.process_type == "noop@2",
            )
        )
        assert upgraded_downstream is not None
        dependency = session.get(
            ProcessingDependency,
            (
                upgraded_downstream.process_id,
                DependencyType.DATASET_RELEASE.value,
                "versioned_upstream",
                now.date().isoformat(),
            ),
        )
    assert upgraded_upstream is not None
    assert stable_v2_task is None
    assert dependency is not None
    assert dependency.resolved_release_process_id == upgraded_upstream.process_id


def test_stock_daily_invalidation_locks_task_before_dependency() -> None:
    now = datetime(2037, 1, 24, 8, tzinfo=TIMEZONE)
    business_date = now.date()
    old_core_batch_id = uuid4()
    new_core_batch_id = uuid4()
    limit_batch_id = uuid4()
    old_core_process_id = uuid4()
    new_core_process_id = uuid4()
    limit_process_id = uuid4()
    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            (
                CollectionBatch(
                    batch_id=old_core_batch_id,
                    batch_type=BatchType.BACKFILL.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(hours=2),
                ),
                CollectionBatch(
                    batch_id=new_core_batch_id,
                    batch_type=BatchType.REPAIR.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now,
                ),
                CollectionBatch(
                    batch_id=limit_batch_id,
                    batch_type=BatchType.BACKFILL.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(hours=1),
                ),
                ProcessingTask(
                    process_id=old_core_process_id,
                    source_batch_id=old_core_batch_id,
                    process_type="stock_daily_core@3",
                    business_date=business_date,
                    output_dataset="stock_daily.core",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.SUCCESS.value,
                    priority=400,
                ),
                ProcessingTask(
                    process_id=new_core_process_id,
                    source_batch_id=new_core_batch_id,
                    process_type="stock_daily_core@4",
                    business_date=business_date,
                    output_dataset="stock_daily.core",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.SUCCESS.value,
                    priority=200,
                ),
                ProcessingTask(
                    process_id=limit_process_id,
                    source_batch_id=limit_batch_id,
                    process_type="stock_daily_limit@1",
                    business_date=business_date,
                    output_dataset="stock_daily.limit",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.SUCCESS.value,
                    priority=400,
                ),
            )
        )
        session.flush()
        session.add_all(
            (
                DatasetRelease(
                    dataset_name="stock_daily.limit",
                    scope_type=ReleaseScope.DATE.value,
                    scope_key=business_date.isoformat(),
                    business_date=business_date,
                    version_id=uuid4(),
                    process_id=limit_process_id,
                    row_count=1,
                    published_at=now - timedelta(hours=1),
                ),
                ProcessingDependency(
                    process_id=limit_process_id,
                    dependency_type=DependencyType.DATASET_RELEASE.value,
                    dependency_name="stock_daily.core",
                    dependency_scope_key=business_date.isoformat(),
                    dependency_scope={
                        "scope_type": ReleaseScope.DATE.value,
                        "scope_key": business_date.isoformat(),
                    },
                    status=DependencyStatus.READY.value,
                    resolved_release_process_id=old_core_process_id,
                ),
            )
        )

    repository = ProcessingRepository(SyncSessionFactory)
    worker_started = Event()
    worker_pid: list[int] = []

    def invalidate_release() -> None:
        with SyncSessionFactory() as session, session.begin():
            assert _lock_publication_scope(
                session,
                dataset_name="stock_daily.core",
                business_date=business_date,
            )
            worker_pid.append(int(session.scalar(text("SELECT pg_backend_pid()"))))
            worker_started.set()
            repository._invalidate_stale_stock_daily_limit(
                session,
                core_process_id=new_core_process_id,
                business_date=business_date,
                now=now,
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with SyncSessionFactory() as planner_session, planner_session.begin():
            locked_task = planner_session.scalar(
                select(ProcessingTask)
                .where(ProcessingTask.process_id == limit_process_id)
                .with_for_update()
            )
            assert locked_task is not None
            future = executor.submit(invalidate_release)
            assert worker_started.wait(timeout=2)
            deadline = monotonic() + 2
            wait_event_type = None
            while monotonic() < deadline:
                wait_event_type = planner_session.scalar(
                    text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :worker_pid"),
                    {"worker_pid": worker_pid[0]},
                )
                if wait_event_type == "Lock":
                    break
                sleep(0.02)
            assert wait_event_type == "Lock"
            planner_session.execute(text("SET LOCAL lock_timeout = '500ms'"))
            planner_session.execute(
                update(ProcessingDependency)
                .where(ProcessingDependency.process_id == limit_process_id)
                .values(blocked_reason="planner dependency refresh")
            )

        future.result(timeout=2)


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


def _versioned_dataset(
    name: str,
    raw_name: str,
    version: str,
    *extra_dependencies: DatasetDependencySpec,
) -> DatasetSpec:
    return DatasetSpec(
        dataset_name=name,
        processor="noop",
        processor_version=version,
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
