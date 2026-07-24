import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.catalog.datasets import ALL_DATASET_SPECS
from app.catalog.specs import DependencyKind, ReleaseScope
from app.db.sync_session import SyncSessionFactory
from app.main import app
from app.modules.acquisition.models import (
    BatchStatus,
    BatchType,
    CollectionBatch,
    CollectionTask,
    CollectionTaskStatus,
)
from app.modules.operations.models import DeferredCollectionStage, OperationCommand
from app.modules.processing.models import (
    DatasetRelease,
    DependencyStatus,
    DependencyType,
    ProcessingDependency,
    ProcessingTask,
    ProcessingTaskStatus,
    ReleaseScopeType,
)
from app.modules.stocks.models import TradeCalendar
from app.modules.topics.models import ConceptBoard, MarketThemeDaily, ThemeIndex
from app.scheduler.jobs import plan_deferred_collection_stages

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)

ADMIN_TOKEN = "integration-admin-token"


def _seed_failed_collection_batch(*, scope_key: str) -> tuple[UUID, UUID, datetime]:
    now = datetime.now(UTC)
    batch_id = uuid4()
    task_id = uuid4()
    scheduled_at = now - timedelta(hours=2)
    with SyncSessionFactory() as session, session.begin():
        session.add(
            CollectionBatch(
                batch_id=batch_id,
                batch_type=BatchType.DAILY.value,
                business_date=date(2026, 7, 15),
                status=BatchStatus.CLOSED.value,
                scheduled_at=scheduled_at,
                plan_version=uuid4().hex,
                expected_task_count=1,
                planning_completed_at=scheduled_at,
                closed_at=now - timedelta(hours=1),
            )
        )
        session.flush()
        session.add(
            CollectionTask(
                task_id=task_id,
                batch_id=batch_id,
                provider="TUSHARE",
                api_name="daily",
                scope_key=scope_key,
                request_params={"trade_date": "20260715"},
                status=CollectionTaskStatus.FAILED.value,
                attempt_count=5,
                max_attempts=5,
                finished_at=now - timedelta(hours=1),
                error_code="TEST_RETRY_RACE",
                error_message="retry race fixture",
            )
        )
    return batch_id, task_id, scheduled_at


def _count_newer_collection_tasks(*, scope_key: str, scheduled_at: datetime) -> int:
    with SyncSessionFactory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(CollectionTask)
            .join(CollectionBatch, CollectionBatch.batch_id == CollectionTask.batch_id)
            .where(
                CollectionTask.provider == "TUSHARE",
                CollectionTask.api_name == "daily",
                CollectionTask.scope_key == scope_key,
                CollectionBatch.scheduled_at > scheduled_at,
            )
        )
    return int(count or 0)


@pytest.mark.asyncio
async def test_collection_retry_requires_exact_scope_for_every_api() -> None:
    now = datetime.now(UTC)
    business_date = date(2036, 7, 15)
    failed_batch_id = uuid4()
    successful_batch_id = uuid4()
    failed_task_id = uuid4()
    failed_scope = "trade_date=20360715;legacy_scope=DC001"
    successful_scope = "trade_date=20360715"

    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            (
                CollectionBatch(
                    batch_id=failed_batch_id,
                    batch_type=BatchType.BACKFILL.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(hours=2),
                    plan_version="1" * 64,
                    expected_task_count=1,
                    planning_completed_at=now - timedelta(hours=2),
                    closed_at=now - timedelta(hours=1),
                ),
                CollectionBatch(
                    batch_id=successful_batch_id,
                    batch_type=BatchType.REPAIR.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(minutes=30),
                    plan_version="2" * 64,
                    expected_task_count=1,
                    planning_completed_at=now - timedelta(minutes=30),
                    closed_at=now - timedelta(minutes=10),
                ),
            )
        )
        session.flush()
        session.add_all(
            (
                CollectionTask(
                    task_id=failed_task_id,
                    batch_id=failed_batch_id,
                    provider="TUSHARE",
                    api_name="dc_concept_cons",
                    scope_key=failed_scope,
                    request_params={"trade_date": "20360715", "legacy_scope": "DC001"},
                    status=CollectionTaskStatus.FAILED.value,
                    attempt_count=3,
                    max_attempts=3,
                    finished_at=now - timedelta(hours=1),
                    error_message="old scope failed",
                ),
                CollectionTask(
                    batch_id=successful_batch_id,
                    provider="TUSHARE",
                    api_name="dc_concept_cons",
                    scope_key=successful_scope,
                    request_params={"trade_date": "20360715"},
                    status=CollectionTaskStatus.SUCCESS.value,
                    attempt_count=1,
                    max_attempts=3,
                    finished_at=now - timedelta(minutes=10),
                    row_count=1,
                ),
            )
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/operations/commands/collection-tasks/{failed_task_id}/retry",
            headers={
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                "Idempotency-Key": "exact-scope-collection-retry",
            },
            json={"reason": "按原任务范围重新采集"},
        )

    assert response.status_code == 202, response.text
    repair_batch_id = UUID(response.json()["result"]["batchId"])
    with SyncSessionFactory() as session:
        repair_task = session.scalar(
            select(CollectionTask).where(CollectionTask.batch_id == repair_batch_id)
        )
    assert repair_task is not None
    assert repair_task.scope_key == failed_scope
    assert repair_task.request_params == {
        "trade_date": "20360715",
        "legacy_scope": "DC001",
    }

    with SyncSessionFactory() as session, session.begin():
        failed_task = session.get(CollectionTask, failed_task_id)
        assert failed_task is not None
        failed_task.status = CollectionTaskStatus.CANCELLED.value
        retry_tasks = tuple(
            session.scalars(
                select(CollectionTask).where(CollectionTask.batch_id == repair_batch_id)
            )
        )
        for retry_task in retry_tasks:
            retry_task.status = CollectionTaskStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_manual_commands_are_authenticated_idempotent_and_queue_only() -> None:
    now = datetime.now(UTC)
    business_date = date(2026, 7, 17)
    source_batch_id = uuid4()
    failed_collection_id = uuid4()
    failed_process_id = uuid4()
    pending_batch_id = uuid4()
    pending_task_id = uuid4()
    queued_process_id = uuid4()

    with SyncSessionFactory() as session, session.begin():
        session.add(
            TradeCalendar(
                exchange="SSE",
                cal_date=business_date,
                is_open=True,
                pretrade_date=business_date - timedelta(days=1),
                synced_at=now,
            )
        )
        session.add_all(
            (
                CollectionBatch(
                    batch_id=source_batch_id,
                    batch_type=BatchType.DAILY.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(hours=2),
                    plan_version="a" * 64,
                    expected_task_count=1,
                    planning_completed_at=now - timedelta(hours=2),
                    closed_at=now - timedelta(hours=1),
                ),
                CollectionBatch(
                    batch_id=pending_batch_id,
                    batch_type=BatchType.REPAIR.value,
                    business_date=business_date,
                    status=BatchStatus.PENDING.value,
                    scheduled_at=now - timedelta(minutes=30),
                    plan_version="b" * 64,
                    expected_task_count=1,
                    planning_completed_at=now - timedelta(minutes=30),
                ),
            )
        )
        session.flush()
        session.add_all(
            (
                CollectionTask(
                    task_id=failed_collection_id,
                    batch_id=source_batch_id,
                    provider="TUSHARE",
                    api_name="daily",
                    scope_key="trade_date=20260717",
                    request_params={"trade_date": "20260717"},
                    status=CollectionTaskStatus.FAILED.value,
                    attempt_count=5,
                    max_attempts=5,
                    error_code="TEST_FAILURE",
                    error_message="test failure",
                ),
                CollectionTask(
                    task_id=pending_task_id,
                    batch_id=pending_batch_id,
                    provider="TUSHARE",
                    api_name="daily_basic",
                    scope_key="trade_date=20260717",
                    request_params={"trade_date": "20260717"},
                    status=CollectionTaskStatus.PENDING.value,
                    max_attempts=5,
                ),
            )
        )
        session.flush()
        session.add_all(
            (
                ProcessingTask(
                    process_id=failed_process_id,
                    source_batch_id=source_batch_id,
                    process_type="test@1",
                    business_date=business_date,
                    output_dataset="command_retry_test",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.FAILED.value,
                    priority=100,
                    attempt_count=3,
                    max_attempts=3,
                    error_message="test failure",
                ),
                ProcessingTask(
                    process_id=queued_process_id,
                    source_batch_id=source_batch_id,
                    process_type="test@1",
                    business_date=business_date,
                    output_dataset="command_cancel_test",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.QUEUED.value,
                    priority=100,
                    queued_at=now,
                ),
            )
        )
        session.flush()
        session.add(
            ProcessingDependency(
                process_id=failed_process_id,
                dependency_type=DependencyType.RAW_ASSET.value,
                dependency_name="daily",
                dependency_scope_key="trade_date=20260717",
                dependency_scope={"trade_date": "20260717"},
                status=DependencyStatus.READY.value,
            )
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.post(
            f"/api/v1/operations/commands/collection-tasks/{failed_collection_id}/retry",
            headers={"Idempotency-Key": "unauthorized-command"},
            json={"reason": "验证未授权请求"},
        )
        assert unauthorized.status_code == 401

        headers = {
            "Authorization": f"Bearer {ADMIN_TOKEN}",
            "Idempotency-Key": "retry-collection-command",
        }
        first = await client.post(
            f"/api/v1/operations/commands/collection-tasks/{failed_collection_id}/retry",
            headers=headers,
            json={"reason": "人工验证后重新采集"},
        )
        repeated = await client.post(
            f"/api/v1/operations/commands/collection-tasks/{failed_collection_id}/retry",
            headers=headers,
            json={"reason": "人工验证后重新采集"},
        )
        conflict = await client.post(
            f"/api/v1/operations/commands/collection-tasks/{failed_collection_id}/retry",
            headers=headers,
            json={"reason": "复用幂等键但修改原因"},
        )
        retry_processing = await client.post(
            f"/api/v1/operations/commands/processing-tasks/{failed_process_id}/retry",
            headers={
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                "Idempotency-Key": "retry-processing-command",
            },
            json={"reason": "依赖已经恢复，重新加工"},
        )
        cancel_processing = await client.post(
            f"/api/v1/operations/commands/processing-tasks/{queued_process_id}/cancel",
            headers={
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                "Idempotency-Key": "cancel-processing-command",
            },
            json={"reason": "人工取消错误的加工任务"},
        )
        cancel_batch = await client.post(
            f"/api/v1/operations/commands/acquisition-batches/{pending_batch_id}/cancel",
            headers={
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                "Idempotency-Key": "cancel-batch-command",
            },
            json={"reason": "人工取消错误的采集批次"},
        )
        backfill = await client.post(
            "/api/v1/operations/commands/backfills",
            headers={
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                "Idempotency-Key": "create-backfill-command",
            },
            json={
                "startDate": business_date.isoformat(),
                "endDate": business_date.isoformat(),
                "apiNames": ["daily"],
                "reason": "回填",
            },
        )
        staged_repair = await client.post(
            "/api/v1/operations/commands/repairs",
            headers={
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                "Idempotency-Key": "create-staged-repair-command",
            },
            json={
                "businessDate": business_date.isoformat(),
                "apiNames": ["dc_concept", "dc_concept_cons", "ths_index", "ths_member"],
                "reason": "验证动态接口自动分阶段修复",
            },
        )

    assert first.status_code == 202, first.text
    assert repeated.status_code == 202, repeated.text
    assert repeated.json() == first.json()
    assert conflict.status_code == 409
    assert retry_processing.status_code == 202, retry_processing.text
    assert cancel_processing.status_code == 202, cancel_processing.text
    assert cancel_batch.status_code == 202, cancel_batch.text
    assert backfill.status_code == 202, backfill.text
    assert backfill.json()["result"]["batchCount"] == 1
    assert staged_repair.status_code == 202, staged_repair.text
    assert staged_repair.json()["result"]["deferredStageCount"] == 1

    with SyncSessionFactory() as session:
        repair_batch_id = first.json()["result"]["batchId"]
        repair_batch = session.get(CollectionBatch, repair_batch_id)
        failed_process = session.get(ProcessingTask, failed_process_id)
        cancelled_process = session.get(ProcessingTask, queued_process_id)
        cancelled_batch = session.get(CollectionBatch, pending_batch_id)
        cancelled_task = session.get(CollectionTask, pending_task_id)
        command_count = session.scalar(select(func.count()).select_from(OperationCommand))
        command = session.scalar(
            select(OperationCommand).where(
                OperationCommand.idempotency_key == "retry-collection-command"
            )
        )
        staged_command_id = UUID(staged_repair.json()["commandId"])
        staged_batch_id = UUID(staged_repair.json()["result"]["batchId"])
        deferred_stages = tuple(
            session.scalars(
                select(DeferredCollectionStage).where(
                    DeferredCollectionStage.command_id == staged_command_id
                )
            )
        )
        staged_tasks = tuple(
            session.scalars(
                select(CollectionTask).where(CollectionTask.batch_id == staged_batch_id)
            )
        )

    assert repair_batch is not None
    assert repair_batch.batch_type == BatchType.REPAIR.value
    assert failed_process is not None
    assert failed_process.status == ProcessingTaskStatus.QUEUED.value
    assert failed_process.max_attempts == 4
    assert cancelled_process is not None
    assert cancelled_process.status == ProcessingTaskStatus.CANCELLED.value
    assert cancelled_batch is not None
    assert cancelled_batch.status == BatchStatus.CANCELLED.value
    assert cancelled_task is not None
    assert cancelled_task.status == CollectionTaskStatus.CANCELLED.value
    assert command_count == 6
    assert command is not None
    assert command.request_id != "unknown"
    assert {stage.api_name for stage in deferred_stages} == {"ths_member"}
    assert {task.api_name for task in staged_tasks} == {
        "dc_concept",
        "dc_concept_cons",
        "ths_index",
    }

    published_at = datetime.now(UTC) + timedelta(seconds=1)
    release_rows = (
        ("market_theme_daily", ReleaseScopeType.DATE.value, business_date.isoformat()),
        ("concept_board", ReleaseScopeType.GLOBAL.value, "global"),
        ("theme_index", ReleaseScopeType.GLOBAL.value, "global"),
    )
    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            (
                MarketThemeDaily(
                    source="DC",
                    theme_code="DC001",
                    trade_date=business_date,
                    name="测试题材",
                    synced_at=published_at,
                ),
                ConceptBoard(
                    source="THS",
                    ts_code="885001.TI",
                    name="测试概念",
                    board_type="N",
                    synced_at=published_at,
                ),
                ThemeIndex(
                    source="THS",
                    ts_code="700001.TI",
                    name="测试主题",
                    theme_type="TH",
                    synced_at=published_at,
                ),
            )
        )
        for dataset_name, scope_type, scope_key in release_rows:
            process_id = uuid4()
            version_id = uuid4()
            session.add(
                ProcessingTask(
                    process_id=process_id,
                    source_batch_id=staged_batch_id,
                    process_type=f"{dataset_name}@1",
                    business_date=(
                        business_date if scope_type == ReleaseScopeType.DATE.value else None
                    ),
                    output_dataset=dataset_name,
                    output_version=version_id,
                    status=ProcessingTaskStatus.SUCCESS.value,
                    priority=100,
                    finished_at=published_at,
                )
            )
            session.flush()
            session.add(
                DatasetRelease(
                    dataset_name=dataset_name,
                    scope_type=scope_type,
                    scope_key=scope_key,
                    business_date=(
                        business_date if scope_type == ReleaseScopeType.DATE.value else None
                    ),
                    version_id=version_id,
                    process_id=process_id,
                    row_count=1,
                    published_at=published_at,
                )
            )

    plan_deferred_collection_stages()

    with SyncSessionFactory() as session:
        planned_stages = tuple(
            session.scalars(
                select(DeferredCollectionStage).where(
                    DeferredCollectionStage.command_id == staged_command_id
                )
            )
        )
        planned_batch_ids = tuple(
            stage.batch_id for stage in planned_stages if stage.batch_id is not None
        )
        deferred_tasks = tuple(
            session.scalars(
                select(CollectionTask).where(CollectionTask.batch_id.in_(planned_batch_ids))
            )
        )

    assert {stage.status for stage in planned_stages} == {"PLANNED"}
    assert len(planned_batch_ids) == 1
    assert {task.api_name for task in deferred_tasks} == {"ths_member"}


@pytest.mark.asyncio
async def test_bulk_retry_queues_unresolved_collection_and_processing_tasks() -> None:
    now = datetime.now(UTC)
    business_date = date(2026, 7, 18)
    source_batch_id = uuid4()
    duplicate_batch_id = uuid4()
    older_daily_task_id = uuid4()
    latest_daily_task_id = uuid4()
    daily_basic_task_id = uuid4()
    ready_process_id = uuid4()
    legacy_core_process_id = uuid4()
    duplicate_process_id = uuid4()
    blocked_process_id = uuid4()
    unknown_stock_process_id = uuid4()
    active_failed_process_id = uuid4()
    active_process_id = uuid4()

    with SyncSessionFactory() as session, session.begin():
        session.add_all(
            (
                CollectionBatch(
                    batch_id=source_batch_id,
                    batch_type=BatchType.DAILY.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(hours=2),
                    plan_version="c" * 64,
                    expected_task_count=2,
                    planning_completed_at=now - timedelta(hours=2),
                    closed_at=now - timedelta(hours=1),
                ),
                CollectionBatch(
                    batch_id=duplicate_batch_id,
                    batch_type=BatchType.REPAIR.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(minutes=90),
                    plan_version="d" * 64,
                    expected_task_count=1,
                    planning_completed_at=now - timedelta(minutes=90),
                    closed_at=now - timedelta(minutes=40),
                ),
            )
        )
        session.add_all(
            (
                CollectionTask(
                    task_id=older_daily_task_id,
                    batch_id=source_batch_id,
                    provider="TUSHARE",
                    api_name="daily",
                    scope_key="trade_date=20260718",
                    request_params={"trade_date": "20260718"},
                    status=CollectionTaskStatus.FAILED.value,
                    attempt_count=3,
                    max_attempts=3,
                    finished_at=now - timedelta(hours=1),
                    error_message="test failure",
                ),
                CollectionTask(
                    task_id=latest_daily_task_id,
                    batch_id=duplicate_batch_id,
                    provider="TUSHARE",
                    api_name="daily",
                    scope_key="trade_date=20260718",
                    request_params={"trade_date": "20260718"},
                    status=CollectionTaskStatus.FAILED.value,
                    attempt_count=3,
                    max_attempts=3,
                    finished_at=now - timedelta(minutes=45),
                    error_message="duplicate test failure",
                ),
                CollectionTask(
                    task_id=daily_basic_task_id,
                    batch_id=source_batch_id,
                    provider="TUSHARE",
                    api_name="daily_basic",
                    scope_key="trade_date=20260718",
                    request_params={"trade_date": "20260718"},
                    status=CollectionTaskStatus.FAILED.value,
                    attempt_count=3,
                    max_attempts=3,
                    finished_at=now - timedelta(hours=1),
                    error_message="test failure",
                ),
                ProcessingTask(
                    process_id=ready_process_id,
                    source_batch_id=source_batch_id,
                    process_type="bulk_ready@1",
                    business_date=business_date,
                    output_dataset="bulk_ready_dataset",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.FAILED.value,
                    priority=100,
                    attempt_count=3,
                    max_attempts=3,
                    finished_at=now - timedelta(minutes=30),
                    error_message="test failure",
                ),
                ProcessingTask(
                    process_id=legacy_core_process_id,
                    source_batch_id=source_batch_id,
                    process_type="stock_daily_core@3",
                    business_date=business_date,
                    output_dataset="stock_daily.core",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.FAILED.value,
                    priority=100,
                    attempt_count=1,
                    max_attempts=3,
                    finished_at=now - timedelta(minutes=30),
                    error_message="daily_basic enrichment quality threshold exceeded",
                ),
                ProcessingTask(
                    process_id=duplicate_process_id,
                    source_batch_id=source_batch_id,
                    process_type="bulk_ready@1",
                    business_date=business_date,
                    output_dataset="bulk_ready_dataset",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.FAILED.value,
                    priority=100,
                    attempt_count=3,
                    max_attempts=3,
                    finished_at=now - timedelta(minutes=35),
                    error_message="duplicate test failure",
                ),
                ProcessingTask(
                    process_id=blocked_process_id,
                    source_batch_id=source_batch_id,
                    process_type="bulk_blocked@1",
                    business_date=business_date,
                    output_dataset="bulk_blocked_dataset",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.FAILED.value,
                    priority=100,
                    attempt_count=3,
                    max_attempts=3,
                    finished_at=now - timedelta(minutes=30),
                    error_message="test failure",
                ),
                ProcessingTask(
                    process_id=unknown_stock_process_id,
                    source_batch_id=source_batch_id,
                    process_type="stock_moneyflow_daily@1",
                    business_date=business_date,
                    output_dataset="stock_moneyflow_daily",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.FAILED.value,
                    priority=100,
                    attempt_count=3,
                    max_attempts=3,
                    finished_at=now - timedelta(minutes=30),
                    error_message="dataset references unknown stocks: ['699997.SH']",
                ),
                ProcessingTask(
                    process_id=active_failed_process_id,
                    source_batch_id=source_batch_id,
                    process_type="bulk_active@1",
                    business_date=business_date,
                    output_dataset="bulk_active_dataset",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.FAILED.value,
                    priority=100,
                    attempt_count=3,
                    max_attempts=3,
                    finished_at=now - timedelta(minutes=30),
                    error_message="test failure",
                ),
                ProcessingTask(
                    process_id=active_process_id,
                    source_batch_id=source_batch_id,
                    process_type="bulk_active@1",
                    business_date=business_date,
                    output_dataset="bulk_active_dataset",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.QUEUED.value,
                    priority=100,
                    queued_at=now - timedelta(minutes=10),
                ),
            )
        )
        session.flush()
        session.add_all(
            (
                ProcessingDependency(
                    process_id=ready_process_id,
                    dependency_type=DependencyType.RAW_ASSET.value,
                    dependency_name="daily",
                    dependency_scope_key="trade_date=20260718",
                    dependency_scope={"trade_date": "20260718"},
                    status=DependencyStatus.READY.value,
                ),
                *(
                    ProcessingDependency(
                        process_id=legacy_core_process_id,
                        dependency_type=DependencyType.RAW_ASSET.value,
                        dependency_name=dependency_name,
                        dependency_scope_key="trade_date=20260718",
                        dependency_scope={"trade_date": "20260718"},
                        status=DependencyStatus.READY.value,
                    )
                    for dependency_name in ("daily", "daily_basic", "adj_factor")
                ),
                ProcessingDependency(
                    process_id=duplicate_process_id,
                    dependency_type=DependencyType.RAW_ASSET.value,
                    dependency_name="daily",
                    dependency_scope_key="trade_date=20260718",
                    dependency_scope={"trade_date": "20260718"},
                    status=DependencyStatus.READY.value,
                ),
                ProcessingDependency(
                    process_id=blocked_process_id,
                    dependency_type=DependencyType.RAW_ASSET.value,
                    dependency_name="daily_basic",
                    dependency_scope_key="trade_date=20260718",
                    dependency_scope={"trade_date": "20260718"},
                    status=DependencyStatus.MISSING.value,
                ),
                ProcessingDependency(
                    process_id=unknown_stock_process_id,
                    dependency_type=DependencyType.RAW_ASSET.value,
                    dependency_name="moneyflow",
                    dependency_scope_key="trade_date=20260718",
                    dependency_scope={"trade_date": "20260718"},
                    status=DependencyStatus.READY.value,
                ),
                ProcessingDependency(
                    process_id=active_failed_process_id,
                    dependency_type=DependencyType.RAW_ASSET.value,
                    dependency_name="daily",
                    dependency_scope_key="trade_date=20260718",
                    dependency_scope={"trade_date": "20260718"},
                    status=DependencyStatus.READY.value,
                ),
            )
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        collection_retry = await client.post(
            "/api/v1/operations/commands/collection-tasks/retry-all-failed",
            headers={
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                "Idempotency-Key": "bulk-retry-collection-command",
            },
            json={"reason": "批量重试失败采集任务"},
        )
        processing_retry = await client.post(
            "/api/v1/operations/commands/processing-tasks/retry-all-failed",
            headers={
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                "Idempotency-Key": "bulk-retry-processing-command",
            },
            json={"reason": "批量重试失败加工任务"},
        )
        unresolved_collection = await client.get(
            "/api/v1/operations/runs"
            "?runType=acquisition&status=failed&unresolvedOnly=true&pageSize=200"
        )
        unresolved_processing = await client.get(
            "/api/v1/operations/runs"
            "?runType=processing&status=failed&unresolvedOnly=true&pageSize=200"
        )
        alerts = await client.get("/api/v1/operations/alerts?pageSize=200")

    assert collection_retry.status_code == 202, collection_retry.text
    assert collection_retry.json()["result"]["retryCount"] == 2
    assert collection_retry.json()["result"]["batchCount"] == 1
    assert collection_retry.json()["result"]["deduplicatedCount"] == 1
    assert len(collection_retry.json()["result"]["batchIds"]) == 1
    assert processing_retry.status_code == 202, processing_retry.text
    assert processing_retry.json()["result"] == {
        "retryCount": 3,
        "skippedDependencyCount": 1,
        "skippedRootCauseCount": 0,
        "skippedUnchangedCount": 0,
        "deduplicatedCount": 1,
        "skippedActiveCount": 1,
    }
    assert unresolved_collection.status_code == 200, unresolved_collection.text
    assert unresolved_processing.status_code == 200, unresolved_processing.text
    assert alerts.status_code == 200, alerts.text
    retried_collection_ids = {
        str(older_daily_task_id),
        str(latest_daily_task_id),
        str(daily_basic_task_id),
    }
    assert retried_collection_ids.isdisjoint(
        {item["id"] for item in unresolved_collection.json()["items"]}
    )
    assert str(duplicate_process_id) not in {
        item["id"] for item in unresolved_processing.json()["items"]
    }
    assert str(active_failed_process_id) not in {
        item["id"] for item in unresolved_processing.json()["items"]
    }
    alert_ids = {item["id"] for item in alerts.json()["items"]}
    assert {f"acquisition:{task_id}" for task_id in retried_collection_ids}.isdisjoint(alert_ids)
    assert f"processing:{duplicate_process_id}" not in alert_ids
    assert f"processing:{active_failed_process_id}" not in alert_ids

    repair_batch_id = UUID(collection_retry.json()["result"]["batchIds"][0])
    with SyncSessionFactory() as session:
        repair_batch = session.get(CollectionBatch, repair_batch_id)
        repair_tasks = tuple(
            session.scalars(
                select(CollectionTask).where(CollectionTask.batch_id == repair_batch_id)
            )
        )
        ready_process = session.get(ProcessingTask, ready_process_id)
        duplicate_process = session.get(ProcessingTask, duplicate_process_id)
        blocked_process = session.get(ProcessingTask, blocked_process_id)
        unknown_stock_process = session.get(ProcessingTask, unknown_stock_process_id)
        current_moneyflow_process = session.scalar(
            select(ProcessingTask).where(
                ProcessingTask.source_batch_id == source_batch_id,
                ProcessingTask.output_dataset == "stock_moneyflow_daily",
                ProcessingTask.process_type == "stock_moneyflow_daily@2",
            )
        )
        legacy_core_process = session.get(ProcessingTask, legacy_core_process_id)
        current_core_process = session.scalar(
            select(ProcessingTask).where(
                ProcessingTask.source_batch_id == source_batch_id,
                ProcessingTask.output_dataset == "stock_daily.core",
                ProcessingTask.process_type == "stock_daily_core@6",
            )
        )
        current_core_dependencies = (
            ()
            if current_core_process is None
            else tuple(
                session.scalars(
                    select(ProcessingDependency).where(
                        ProcessingDependency.process_id == current_core_process.process_id
                    )
                )
            )
        )

    assert repair_batch is not None
    assert repair_batch.expected_task_count == 2
    assert {task.api_name for task in repair_tasks} == {"daily", "daily_basic"}
    assert ready_process is not None
    assert ready_process.status == ProcessingTaskStatus.QUEUED.value
    assert ready_process.max_attempts == 4
    assert duplicate_process is not None
    assert duplicate_process.status == ProcessingTaskStatus.FAILED.value
    assert blocked_process is not None
    assert blocked_process.status == ProcessingTaskStatus.FAILED.value
    assert unknown_stock_process is not None
    assert unknown_stock_process.status == ProcessingTaskStatus.FAILED.value
    assert unknown_stock_process.attempt_count == 3
    assert current_moneyflow_process is not None
    assert current_moneyflow_process.process_id != unknown_stock_process_id
    assert current_moneyflow_process.status == ProcessingTaskStatus.QUEUED.value
    assert legacy_core_process is not None
    assert legacy_core_process.status == ProcessingTaskStatus.FAILED.value
    assert current_core_process is not None
    assert current_core_process.process_id != legacy_core_process_id
    assert current_core_process.status == ProcessingTaskStatus.QUEUED.value
    assert {dependency.dependency_name for dependency in current_core_dependencies} == {
        "daily",
        "daily_basic",
        "adj_factor",
    }


@pytest.mark.asyncio
async def test_release_gap_recovery_plans_only_missing_dataset_apis() -> None:
    now = datetime.now(UTC)
    business_date = date(2026, 7, 16)
    source_batch_id = uuid4()
    active_batch_id = uuid4()
    released_process_id = uuid4()
    released_version_id = uuid4()

    with SyncSessionFactory() as session, session.begin():
        session.add(
            TradeCalendar(
                exchange="SSE",
                cal_date=business_date,
                is_open=True,
                pretrade_date=business_date - timedelta(days=1),
                synced_at=now,
            )
        )
        session.add_all(
            (
                CollectionBatch(
                    batch_id=source_batch_id,
                    batch_type=BatchType.DAILY.value,
                    business_date=business_date,
                    status=BatchStatus.CLOSED.value,
                    scheduled_at=now - timedelta(hours=2),
                    plan_version="e" * 64,
                    expected_task_count=1,
                    planning_completed_at=now - timedelta(hours=2),
                    closed_at=now - timedelta(hours=1),
                ),
                CollectionBatch(
                    batch_id=active_batch_id,
                    batch_type=BatchType.REPAIR.value,
                    business_date=business_date,
                    status=BatchStatus.PENDING.value,
                    scheduled_at=now - timedelta(minutes=10),
                    plan_version="f" * 64,
                    expected_task_count=1,
                    planning_completed_at=now - timedelta(minutes=10),
                ),
            )
        )
        session.flush()
        session.add_all(
            (
                ProcessingTask(
                    process_id=released_process_id,
                    source_batch_id=source_batch_id,
                    process_type="stock_daily_core@1",
                    business_date=business_date,
                    output_dataset="stock_daily.core",
                    output_version=released_version_id,
                    status=ProcessingTaskStatus.SUCCESS.value,
                    priority=100,
                    finished_at=now - timedelta(hours=1),
                ),
                CollectionTask(
                    batch_id=active_batch_id,
                    provider="TUSHARE",
                    api_name="top_list",
                    scope_key="trade_date=20260716",
                    request_params={"trade_date": "20260716"},
                    status=CollectionTaskStatus.PENDING.value,
                    max_attempts=5,
                ),
            )
        )
        session.flush()
        session.add(
            DatasetRelease(
                dataset_name="stock_daily.core",
                scope_type=ReleaseScopeType.DATE.value,
                scope_key=business_date.isoformat(),
                business_date=business_date,
                version_id=released_version_id,
                process_id=released_process_id,
                row_count=1,
                published_at=now - timedelta(hours=1),
            )
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/operations/commands/release-gaps/backfill",
            headers={
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                "Idempotency-Key": "recover-release-gaps-command",
            },
            json={
                "startDate": business_date.isoformat(),
                "endDate": business_date.isoformat(),
                "reason": "补齐数据发布缺失",
            },
        )

    assert response.status_code == 202, response.text
    result = response.json()["result"]
    assert result["batchCount"] == 1
    assert result["missingDateCount"] == 1
    assert result["skippedActiveApiCount"] == 1

    date_specs = tuple(
        spec for spec in ALL_DATASET_SPECS if spec.release_scope == ReleaseScope.DATE
    )
    expected_api_names = {
        dependency.name
        for spec in date_specs
        if spec.dataset_name != "stock_daily.core"
        for dependency in spec.dependencies
        if dependency.kind == DependencyKind.RAW_ASSET
    } - {"top_list"}
    assert result["missingDatasetCount"] == len(date_specs) - 1
    assert result["plannedApiCount"] == len(expected_api_names)

    recovery_batch_id = UUID(result["batchIds"][0])
    with SyncSessionFactory() as session:
        recovery_batch = session.get(CollectionBatch, recovery_batch_id)
        recovery_tasks = tuple(
            session.scalars(
                select(CollectionTask).where(CollectionTask.batch_id == recovery_batch_id)
            )
        )

    assert recovery_batch is not None
    assert recovery_batch.batch_type == BatchType.BACKFILL.value
    assert recovery_batch.business_date == business_date
    assert {task.api_name for task in recovery_tasks} == expected_api_names
    assert {"daily", "daily_basic", "adj_factor", "top_list"}.isdisjoint(
        {task.api_name for task in recovery_tasks}
    )


@pytest.mark.asyncio
async def test_single_and_batch_collection_retry_share_one_concurrency_domain() -> None:
    scope_key = f"retry-race-single-batch={uuid4().hex}"
    batch_id, task_id, scheduled_at = _seed_failed_collection_batch(scope_key=scope_key)
    transport = httpx.ASGITransport(app=app)

    with SyncSessionFactory() as blocker, blocker.begin():
        locked_task = blocker.scalar(
            select(CollectionTask).where(CollectionTask.task_id == task_id).with_for_update()
        )
        assert locked_task is not None
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://test") as first_client,
            httpx.AsyncClient(transport=transport, base_url="http://test") as second_client,
        ):
            batch_request = asyncio.create_task(
                first_client.post(
                    f"/api/v1/operations/commands/acquisition-batches/{batch_id}"
                    "/retry-failed-tasks",
                    headers={
                        "Authorization": f"Bearer {ADMIN_TOKEN}",
                        "Idempotency-Key": f"retry-race-batch-{uuid4().hex}",
                    },
                    json={"reason": "并发批次重试"},
                )
            )
            single_request = asyncio.create_task(
                second_client.post(
                    f"/api/v1/operations/commands/collection-tasks/{task_id}/retry",
                    headers={
                        "Authorization": f"Bearer {ADMIN_TOKEN}",
                        "Idempotency-Key": f"retry-race-single-{uuid4().hex}",
                    },
                    json={"reason": "并发单任务重试"},
                )
            )
            await asyncio.sleep(0.2)

    responses = await asyncio.gather(batch_request, single_request)
    assert sorted(response.status_code for response in responses) == [202, 409]
    assert (
        _count_newer_collection_tasks(
            scope_key=scope_key,
            scheduled_at=scheduled_at,
        )
        == 1
    )


@pytest.mark.asyncio
async def test_bulk_and_batch_collection_retry_share_one_concurrency_domain() -> None:
    scope_key = f"retry-race-bulk-batch={uuid4().hex}"
    batch_id, task_id, scheduled_at = _seed_failed_collection_batch(scope_key=scope_key)
    transport = httpx.ASGITransport(app=app)

    with SyncSessionFactory() as blocker, blocker.begin():
        locked_task = blocker.scalar(
            select(CollectionTask).where(CollectionTask.task_id == task_id).with_for_update()
        )
        assert locked_task is not None
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://test") as first_client,
            httpx.AsyncClient(transport=transport, base_url="http://test") as second_client,
        ):
            batch_request = asyncio.create_task(
                first_client.post(
                    f"/api/v1/operations/commands/acquisition-batches/{batch_id}"
                    "/retry-failed-tasks",
                    headers={
                        "Authorization": f"Bearer {ADMIN_TOKEN}",
                        "Idempotency-Key": f"retry-race-batch-{uuid4().hex}",
                    },
                    json={"reason": "并发批次重试"},
                )
            )
            bulk_request = asyncio.create_task(
                second_client.post(
                    "/api/v1/operations/commands/collection-tasks/retry-all-failed",
                    headers={
                        "Authorization": f"Bearer {ADMIN_TOKEN}",
                        "Idempotency-Key": f"retry-race-bulk-{uuid4().hex}",
                    },
                    json={"reason": "并发全部重试"},
                )
            )
            await asyncio.sleep(0.2)

    responses = await asyncio.gather(batch_request, bulk_request)
    assert any(response.status_code == 202 for response in responses)
    assert all(response.status_code in {202, 409} for response in responses)
    assert (
        _count_newer_collection_tasks(
            scope_key=scope_key,
            scheduled_at=scheduled_at,
        )
        == 1
    )
