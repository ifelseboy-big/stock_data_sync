import os
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

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
    assert staged_repair.json()["result"]["deferredStageCount"] == 2

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
    assert {stage.api_name for stage in deferred_stages} == {"dc_concept_cons", "ths_member"}
    assert {task.api_name for task in staged_tasks} == {"dc_concept", "ths_index"}

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
    assert len(planned_batch_ids) == 2
    assert {task.api_name for task in deferred_tasks} == {"dc_concept_cons", "ths_member"}


@pytest.mark.asyncio
async def test_bulk_retry_queues_unresolved_collection_and_processing_tasks() -> None:
    now = datetime.now(UTC)
    business_date = date(2026, 7, 18)
    source_batch_id = uuid4()
    ready_process_id = uuid4()
    blocked_process_id = uuid4()

    with SyncSessionFactory() as session, session.begin():
        session.add(
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
            )
        )
        session.add_all(
            (
                CollectionTask(
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
                ProcessingDependency(
                    process_id=blocked_process_id,
                    dependency_type=DependencyType.RAW_ASSET.value,
                    dependency_name="daily_basic",
                    dependency_scope_key="trade_date=20260718",
                    dependency_scope={"trade_date": "20260718"},
                    status=DependencyStatus.MISSING.value,
                ),
            )
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        collection_retry = await client.post(
            f"/api/v1/operations/commands/acquisition-batches/{source_batch_id}/retry-failed-tasks",
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

    assert collection_retry.status_code == 202, collection_retry.text
    assert collection_retry.json()["result"]["taskCount"] == 2
    assert processing_retry.status_code == 202, processing_retry.text
    assert processing_retry.json()["result"] == {
        "retryCount": 1,
        "skippedDependencyCount": 1,
    }

    repair_batch_id = UUID(collection_retry.json()["result"]["batchId"])
    with SyncSessionFactory() as session:
        repair_batch = session.get(CollectionBatch, repair_batch_id)
        repair_tasks = tuple(
            session.scalars(
                select(CollectionTask).where(CollectionTask.batch_id == repair_batch_id)
            )
        )
        ready_process = session.get(ProcessingTask, ready_process_id)
        blocked_process = session.get(ProcessingTask, blocked_process_id)

    assert repair_batch is not None
    assert repair_batch.expected_task_count == 2
    assert {task.api_name for task in repair_tasks} == {"daily", "daily_basic"}
    assert ready_process is not None
    assert ready_process.status == ProcessingTaskStatus.QUEUED.value
    assert ready_process.max_attempts == 4
    assert blocked_process is not None
    assert blocked_process.status == ProcessingTaskStatus.FAILED.value
