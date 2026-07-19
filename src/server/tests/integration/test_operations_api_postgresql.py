import os
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.db.sync_session import SyncSessionFactory
from app.main import app
from app.modules.acquisition.models import (
    BatchStatus,
    BatchType,
    CollectionBatch,
    CollectionTask,
    CollectionTaskStatus,
)
from app.modules.operations.models import ProviderRequestLog, ScheduledJobExecution
from app.modules.processing.models import (
    DatasetRelease,
    DependencyStatus,
    DependencyType,
    ProcessingDependency,
    ProcessingTask,
    ProcessingTaskStatus,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)

TIMEZONE = ZoneInfo("Asia/Shanghai")


@pytest.mark.asyncio
async def test_operations_read_models_use_runtime_and_provider_records() -> None:
    now = datetime.now(TIMEZONE)
    batch_id = uuid4()
    collection_task_id = uuid4()
    published_process_id = uuid4()
    blocked_process_id = uuid4()
    recovered_failure_id = uuid4()
    recovered_success_id = uuid4()
    scheduler_failure_id = uuid4()
    scheduler_success_id = uuid4()
    with SyncSessionFactory() as session, session.begin():
        session.add(
            CollectionBatch(
                batch_id=batch_id,
                batch_type=BatchType.DAILY.value,
                business_date=now.date(),
                scheduled_at=now - timedelta(minutes=10),
                status=BatchStatus.CLOSED.value,
                expected_task_count=1,
                plan_version="f" * 64,
                planning_completed_at=now - timedelta(minutes=10),
                started_at=now - timedelta(minutes=9),
                closed_at=now - timedelta(minutes=8),
            )
        )
        session.flush()
        session.add(
            CollectionTask(
                task_id=collection_task_id,
                batch_id=batch_id,
                provider="TUSHARE",
                api_name="daily",
                scope_key=f"trade_date={now:%Y%m%d}",
                request_params={"trade_date": f"{now:%Y%m%d}"},
                status=CollectionTaskStatus.SUCCESS.value,
                attempt_count=1,
                max_attempts=3,
                request_count=1,
                row_count=10,
                started_at=now - timedelta(minutes=9),
                finished_at=now - timedelta(minutes=8),
            )
        )
        session.flush()
        session.add(
            ProviderRequestLog(
                task_id=collection_task_id,
                provider="tushare",
                endpoint="daily",
                requested_at=now - timedelta(minutes=9),
                finished_at=now - timedelta(minutes=9, seconds=-1),
                status="SUCCESS",
                duration_ms=1000,
                rate_limit_wait_ms=20,
                row_count=10,
            )
        )
        session.add_all(
            (
                ProcessingTask(
                    process_id=published_process_id,
                    source_batch_id=batch_id,
                    process_type="daily@1",
                    business_date=now.date(),
                    output_dataset="test_daily",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.SUCCESS.value,
                    priority=100,
                    attempt_count=1,
                    max_attempts=3,
                    queued_at=now - timedelta(minutes=7),
                    started_at=now - timedelta(minutes=7),
                    finished_at=now - timedelta(minutes=6),
                    rows_read=10,
                    rows_rejected=0,
                    rows_written=10,
                ),
                ProcessingTask(
                    process_id=blocked_process_id,
                    source_batch_id=batch_id,
                    process_type="blocked@1",
                    business_date=now.date(),
                    output_dataset="blocked_daily",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.BLOCKED.value,
                    priority=100,
                    attempt_count=0,
                    max_attempts=3,
                    queued_at=now - timedelta(minutes=5),
                    error_message="required asset is missing",
                ),
                ProcessingTask(
                    process_id=recovered_failure_id,
                    source_batch_id=batch_id,
                    process_type="recovered@1",
                    business_date=now.date(),
                    output_dataset="recovered_daily",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.FAILED.value,
                    priority=100,
                    attempt_count=1,
                    max_attempts=3,
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4),
                    error_message="temporary failure",
                ),
                ProcessingTask(
                    process_id=recovered_success_id,
                    source_batch_id=batch_id,
                    process_type="recovered@1",
                    business_date=now.date(),
                    output_dataset="recovered_daily",
                    output_version=uuid4(),
                    status=ProcessingTaskStatus.SUCCESS.value,
                    priority=100,
                    attempt_count=1,
                    max_attempts=3,
                    started_at=now - timedelta(minutes=3),
                    finished_at=now - timedelta(minutes=2),
                ),
            )
        )
        session.flush()
        session.add(
            ProcessingDependency(
                process_id=blocked_process_id,
                dependency_type=DependencyType.RAW_ASSET.value,
                dependency_name="missing_api",
                dependency_scope_key="date",
                dependency_scope={"trade_date": f"{now:%Y%m%d}"},
                status=DependencyStatus.MISSING.value,
                blocked_reason="required asset is missing",
            )
        )
        session.add(
            DatasetRelease(
                dataset_name="test_daily",
                scope_type="DATE",
                scope_key=now.date().isoformat(),
                business_date=now.date(),
                version_id=uuid4(),
                process_id=published_process_id,
                row_count=10,
                published_at=now - timedelta(minutes=6),
            )
        )
        session.add_all(
            (
                ScheduledJobExecution(
                    execution_id=scheduler_failure_id,
                    job_id="test-recovered-job",
                    trigger_type="SCHEDULED",
                    status="FAILED",
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4),
                    error_message="temporary failure",
                    created_at=now - timedelta(minutes=5),
                ),
                ScheduledJobExecution(
                    execution_id=scheduler_success_id,
                    job_id="test-recovered-job",
                    trigger_type="SCHEDULED",
                    status="SUCCESS",
                    started_at=now - timedelta(minutes=3),
                    finished_at=now - timedelta(minutes=2),
                    created_at=now - timedelta(minutes=3),
                ),
            )
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        overview = await client.get("/api/v1/operations/overview")
        batches = await client.get("/api/v1/operations/acquisition-batches")
        queue = await client.get("/api/v1/operations/processing-queue")
        dependencies = await client.get("/api/v1/operations/dependencies")
        releases = await client.get("/api/v1/operations/releases")
        provider = await client.get("/api/v1/operations/providers/tushare")
        runs = await client.get("/api/v1/operations/runs")
        alerts = await client.get("/api/v1/operations/alerts")
        command_options = await client.get("/api/v1/operations/command-options")
        resources = await client.get("/api/v1/system/resources")

    for response in (
        overview,
        batches,
        queue,
        dependencies,
        releases,
        provider,
        runs,
        alerts,
        command_options,
        resources,
    ):
        assert response.status_code == 200, response.text

    assert overview.json()["metrics"]["blockedTaskCount"] >= 1
    assert overview.json()["metrics"]["providerSuccessRateToday"] == 1.0
    assert any(item["id"] == str(batch_id) for item in batches.json()["items"])
    assert any(item["id"] == str(blocked_process_id) for item in queue.json()["items"])
    readiness = dependencies.json()["items"]
    blocked_readiness = next(item for item in readiness if item["id"] == str(blocked_process_id))
    assert blocked_readiness["readinessStatus"] == "blocked"
    assert blocked_readiness["blockedDependencyCount"] == 1
    assert blocked_readiness["sources"][0]["sourceName"] == "missing_api"
    assert any(item["datasetName"] == "test_daily" for item in releases.json()["items"])
    assert any(item["endpoint"] == "daily" for item in provider.json()["endpoints"])
    assert any(item["id"] == str(collection_task_id) for item in runs.json()["items"])
    assert resources.json()["database"]["sharedBuffersBytes"] > 0
    assert any(item["id"] == f"processing:{blocked_process_id}" for item in alerts.json()["items"])
    assert all(
        item["id"] != f"processing:{recovered_failure_id}" for item in alerts.json()["items"]
    )
    assert all(item["id"] != f"scheduler:{scheduler_failure_id}" for item in alerts.json()["items"])
    assert any(item["apiName"] == "daily" for item in command_options.json()["acquisitionApis"])
    assert resources.json()["database"]["status"] == "ok"
    assert resources.json()["storage"]["totalBytes"] > 0
