from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from app.catalog import ApiSpec
from app.catalog.tushare import (
    ALL_TUSHARE_API_SPECS,
    DAILY_CLOSE_SPECS,
    DAILY_FINAL_SPECS,
    DAILY_LATE_SPECS,
    DAILY_PREOPEN_SPECS,
    MASTER_STOCK_SPECS,
    TRADE_CAL_SPEC,
)
from app.core.config import settings
from app.db.session import engine as async_engine
from app.db.sync_session import SyncSessionFactory, sync_engine
from app.main import app
from app.modules.acquisition.domain import TERMINAL_TASK_STATUSES
from app.modules.acquisition.factory import (
    get_acquisition_repository,
    get_acquisition_runtime,
    get_collection_planner,
    get_raw_asset_store,
    shutdown_acquisition_runtime,
)
from app.modules.acquisition.models import (
    BatchStatus,
    BatchType,
    CollectionBatch,
    CollectionTask,
    CollectionTaskStatus,
    RawDataAsset,
)
from app.modules.acquisition.planner import StagePlan
from app.modules.operations.models import ProviderRequestLog
from app.modules.partitions.service import ensure_monthly_partitions
from app.modules.processing.factory import (
    get_dataset_specs,
    get_processing_repository,
    get_processing_runtime,
)
from app.modules.processing.models import (
    DatasetRelease,
    DependencyStatus,
    ProcessingDependency,
    ProcessingTask,
    ProcessingTaskStatus,
)
from app.modules.stocks.models import (
    Stock,
    StockCompany,
    StockDaily,
    StockMoneyflowDaily,
    StockSuspendDaily,
    StockTechnicalDaily,
    TradeCalendar,
)
from app.storage import RawAssetMetadata

TIMEZONE = ZoneInfo("Asia/Shanghai")
DAILY_SPECS = (
    *DAILY_PREOPEN_SPECS,
    *DAILY_CLOSE_SPECS,
    *DAILY_LATE_SPECS,
    *DAILY_FINAL_SPECS,
)
DAILY_API_NAMES = tuple(spec.api_name for spec in DAILY_SPECS)
SUCCESSFUL_COLLECTION_STATUSES = frozenset(
    {
        CollectionTaskStatus.SUCCESS.value,
        CollectionTaskStatus.EMPTY_VALID.value,
    }
)


class LiveWorkflowError(RuntimeError):
    pass


class OperationsApi:
    def __init__(self) -> None:
        token = settings.admin_api_token.get_secret_value()
        if not token:
            raise LiveWorkflowError("ADMIN_API_TOKEN is required for live workflow validation")
        self._loop = asyncio.new_event_loop()
        self._client = self._loop.run_until_complete(self._open_client(token))

    @staticmethod
    async def _open_client(token: str) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://live-validation",
            headers={"Authorization": f"Bearer {token}"},
        )
        await client.__aenter__()
        return client

    def post(self, path: str, payload: dict[str, object], idempotency_key: str) -> dict[str, Any]:
        response = self._loop.run_until_complete(
            self._client.post(
                path,
                json=payload,
                headers={"Idempotency-Key": idempotency_key},
            )
        )
        if response.status_code != 202:
            raise LiveWorkflowError(
                f"POST {path} failed: {response.status_code} {response.text[:1000]}"
            )
        return dict(response.json())

    def get(self, path: str) -> dict[str, Any]:
        response = self._loop.run_until_complete(self._client.get(path))
        if response.status_code != 200:
            raise LiveWorkflowError(
                f"GET {path} failed: {response.status_code} {response.text[:1000]}"
            )
        return dict(response.json())

    def close(self) -> None:
        self._loop.run_until_complete(self._client.__aexit__(None, None, None))
        self._loop.run_until_complete(async_engine.dispose())
        self._loop.close()


def _progress(message: str) -> None:
    print(message, flush=True)


def _validate_environment() -> str:
    url = make_url(settings.database_url)
    if url.host not in {"localhost", "127.0.0.1"} or url.database != "stock_data_sync":
        raise LiveWorkflowError("live workflow validation only runs against local stock_data_sync")
    if not settings.tushare_token.get_secret_value():
        raise LiveWorkflowError("TUSHARE_TOKEN is not configured")
    with SyncSessionFactory() as session:
        database_name = str(session.scalar(select(func.current_database())))
        version = session.execute(select(func.current_setting("server_version"))).scalar_one()
        alembic_version = session.scalar(text("SELECT version_num FROM alembic_version"))
    if database_name != "stock_data_sync":
        raise LiveWorkflowError(f"unexpected database: {database_name}")
    if alembic_version != "20260719_0005":
        raise LiveWorkflowError(f"database migration is not current: {alembic_version}")
    _progress(f"database={database_name}, postgresql={version}, migration={alembic_version}")
    return database_name


def _ensure_partitions(start_date: date, end_date: date) -> None:
    current = date(start_date.year, start_date.month, 1)
    final = date(end_date.year, end_date.month, 1)
    with sync_engine.begin() as connection:
        while current <= final:
            ensure_monthly_partitions(
                connection,
                reference_date=current,
                months_ahead=0,
            )
            next_month = current.month + 1
            current = date(
                current.year + int(next_month == 13),
                1 if next_month == 13 else next_month,
                1,
            )


def _plan_stage(
    *,
    batch_type: BatchType,
    business_date: date,
    scheduled_at: datetime,
    specs: tuple[ApiSpec, ...],
    finalize: bool,
) -> UUID:
    result = get_collection_planner().plan(
        StagePlan(
            batch_type=batch_type,
            business_date=business_date,
            scheduled_at=scheduled_at,
            api_specs=specs,
            finalize=finalize,
        ),
        now=datetime.now(TIMEZONE),
    )
    if result.batch_id is None or result.plan is None:
        raise LiveWorkflowError(f"stage was skipped for {business_date}")
    _progress(
        f"planned {batch_type.value} {business_date}: "
        f"created={result.plan.created_task_count}, total={result.plan.total_task_count}, "
        f"frozen={result.plan.frozen}"
    )
    return result.batch_id


def _drain_collection() -> int:
    runtime = get_acquisition_runtime()
    submitted_total = 0
    try:
        while True:
            submitted = runtime.dispatch(now=datetime.now(TIMEZONE))
            submitted_total += submitted
            while runtime.inflight_count():
                time.sleep(0.2)
            if submitted == 0:
                return submitted_total
    finally:
        shutdown_acquisition_runtime()


def _close_batches() -> tuple[UUID, ...]:
    return get_acquisition_repository().close_ready_batches(now=datetime.now(TIMEZONE))


def _batch_task_rows(batch_ids: Sequence[UUID]) -> list[tuple[Any, ...]]:
    with SyncSessionFactory() as session:
        return list(
            session.execute(
                select(
                    CollectionTask.task_id,
                    CollectionTask.batch_id,
                    CollectionTask.api_name,
                    CollectionTask.status,
                    CollectionTask.attempt_count,
                    CollectionTask.request_count,
                    CollectionTask.row_count,
                    CollectionTask.error_code,
                    CollectionTask.error_message,
                )
                .where(CollectionTask.batch_id.in_(batch_ids))
                .order_by(CollectionTask.batch_id, CollectionTask.api_name)
            ).all()
        )


def _assert_collection_success(batch_ids: Sequence[UUID], label: str) -> None:
    rows = _batch_task_rows(batch_ids)
    failures = [row for row in rows if row.status not in SUCCESSFUL_COLLECTION_STATUSES]
    if failures:
        details = [
            {
                "taskId": str(row.task_id),
                "api": row.api_name,
                "status": row.status,
                "errorCode": row.error_code,
                "error": str(row.error_message or "")[:300],
            }
            for row in failures
        ]
        raise LiveWorkflowError(f"{label} collection failed: {details}")
    with SyncSessionFactory() as session:
        closed_count = int(
            session.scalar(
                select(func.count())
                .select_from(CollectionBatch)
                .where(
                    CollectionBatch.batch_id.in_(batch_ids),
                    CollectionBatch.status == BatchStatus.CLOSED.value,
                )
            )
            or 0
        )
    if closed_count != len(batch_ids):
        raise LiveWorkflowError(f"{label} batches are not all closed")
    _progress(f"{label}: {len(rows)} collection tasks succeeded in {len(batch_ids)} batches")


def _plan_and_drain_processing(batch_ids: Sequence[UUID], label: str) -> int:
    repository = get_processing_repository()
    result = repository.plan_closed_batches(
        get_dataset_specs().all(),
        now=datetime.now(TIMEZONE),
        source_batch_ids=batch_ids,
    )
    _progress(
        f"{label} processing plan: created={result.created_task_count}, "
        f"queued={result.queued_task_count}, blocked={result.blocked_task_count}"
    )
    transition_count = 0
    while True:
        transition = get_processing_runtime().dispatch(
            now=datetime.now(TIMEZONE),
            source_batch_ids=batch_ids,
        )
        if transition is None:
            break
        transition_count += 1
        if transition.status != ProcessingTaskStatus.SUCCESS:
            raise LiveWorkflowError(
                f"{label} processing failed: {transition.process_id} {transition.status.value}"
            )
    with SyncSessionFactory() as session:
        tasks = list(
            session.scalars(
                select(ProcessingTask).where(ProcessingTask.source_batch_id.in_(batch_ids))
            )
        )
        not_successful = [
            task for task in tasks if task.status != ProcessingTaskStatus.SUCCESS.value
        ]
        dependency_failures = int(
            session.scalar(
                select(func.count())
                .select_from(ProcessingDependency)
                .join(
                    ProcessingTask,
                    ProcessingTask.process_id == ProcessingDependency.process_id,
                )
                .where(
                    ProcessingTask.source_batch_id.in_(batch_ids),
                    ProcessingDependency.status != DependencyStatus.READY.value,
                )
            )
            or 0
        )
    if not_successful or dependency_failures:
        raise LiveWorkflowError(
            f"{label} processing not complete: "
            f"tasks={[(str(item.process_id), item.status) for item in not_successful]}, "
            f"dependencies={dependency_failures}"
        )
    _assert_processing_serial(tasks, label)
    _progress(f"{label}: {len(tasks)} processing tasks succeeded globally serial")
    return transition_count


def _assert_processing_serial(tasks: Sequence[ProcessingTask], label: str) -> None:
    completed = sorted(
        (task for task in tasks if task.started_at is not None and task.finished_at is not None),
        key=lambda task: task.started_at or datetime.min.replace(tzinfo=UTC),
    )
    for previous, current in zip(completed, completed[1:], strict=False):
        if current.started_at is None or previous.finished_at is None:
            continue
        if current.started_at < previous.finished_at:
            raise LiveWorkflowError(
                f"{label} processing overlapped: {previous.process_id} and {current.process_id}"
            )


def _trading_dates(start_date: date, end_date: date) -> tuple[date, ...]:
    with SyncSessionFactory() as session:
        covered = int(
            session.scalar(
                select(func.count())
                .select_from(TradeCalendar)
                .where(
                    TradeCalendar.exchange == "SSE",
                    TradeCalendar.cal_date.between(start_date, end_date),
                )
            )
            or 0
        )
        dates = tuple(
            session.scalars(
                select(TradeCalendar.cal_date)
                .where(
                    TradeCalendar.exchange == "SSE",
                    TradeCalendar.cal_date.between(start_date, end_date),
                    TradeCalendar.is_open.is_(True),
                )
                .order_by(TradeCalendar.cal_date)
            )
        )
    expected = (end_date - start_date).days + 1
    if covered != expected or not dates:
        raise LiveWorkflowError(
            f"trade calendar coverage mismatch: covered={covered}, expected={expected}"
        )
    return dates


def _first_task(batch_id: UUID) -> CollectionTask:
    with SyncSessionFactory() as session:
        task = session.scalar(
            select(CollectionTask)
            .where(CollectionTask.batch_id == batch_id)
            .order_by(CollectionTask.task_id)
        )
        if task is None:
            raise LiveWorkflowError(f"batch has no task: {batch_id}")
        session.expunge(task)
        return task


def _batch_ids_from_command(result: dict[str, Any]) -> tuple[UUID, ...]:
    payload = result.get("result")
    if not isinstance(payload, dict):
        raise LiveWorkflowError(f"command has no result: {result}")
    values = payload.get("batchIds")
    if not isinstance(values, list):
        value = payload.get("batchId")
        values = [value] if isinstance(value, str) else []
    return tuple(UUID(str(value)) for value in values)


def _wait_until_retry_due(task_id: UUID) -> float:
    with SyncSessionFactory() as session:
        task = session.get(CollectionTask, task_id)
        if task is None or task.status != CollectionTaskStatus.RETRY_WAIT.value:
            raise LiveWorkflowError(f"automatic retry task did not enter RETRY_WAIT: {task_id}")
        if task.next_retry_at is None:
            raise LiveWorkflowError("RETRY_WAIT task has no next_retry_at")
        wait_seconds = max(
            0.0,
            (task.next_retry_at - datetime.now(task.next_retry_at.tzinfo)).total_seconds(),
        )
    _progress(f"automatic retry scheduled in {wait_seconds:.1f}s")
    deadline = time.monotonic() + wait_seconds + 0.5
    while time.monotonic() < deadline:
        time.sleep(min(5.0, deadline - time.monotonic()))
    return wait_seconds


def _run_processing_retry(api: OperationsApi, business_date: date) -> dict[str, object]:
    result = api.post(
        "/api/v1/operations/commands/repairs",
        {
            "businessDate": business_date.isoformat(),
            "apiNames": ["moneyflow"],
            "reason": "真实全流程验证：加工文件临时不可用后的人工重试",
        },
        f"live-processing-repair-{business_date:%Y%m%d}-v1",
    )
    batch_id = _batch_ids_from_command(result)[0]
    _drain_collection()
    _close_batches()
    _assert_collection_success((batch_id,), "processing retry source")
    plan = get_processing_repository().plan_closed_batches(
        get_dataset_specs().all(),
        now=datetime.now(TIMEZONE),
    )
    if plan.created_task_count < 1:
        raise LiveWorkflowError("processing retry fixture did not create a processing task")
    with SyncSessionFactory() as session:
        process = session.scalar(
            select(ProcessingTask).where(
                ProcessingTask.source_batch_id == batch_id,
                ProcessingTask.output_dataset == "stock_moneyflow_daily",
            )
        )
        asset = session.scalar(
            select(RawDataAsset)
            .join(CollectionTask, CollectionTask.task_id == RawDataAsset.task_id)
            .where(CollectionTask.batch_id == batch_id)
        )
        if process is None or asset is None:
            raise LiveWorkflowError("processing retry fixture is incomplete")
        process_id = process.process_id
        storage_uri = asset.storage_uri
    asset_path = _file_uri_path(storage_uri)
    fault_path = asset_path.with_name(f"{asset_path.name}.fault-injected")
    if fault_path.exists():
        raise LiveWorkflowError(f"fault injection path already exists: {fault_path}")
    asset_path.rename(fault_path)
    try:
        transition = get_processing_runtime().dispatch(now=datetime.now(TIMEZONE))
        if transition is None or transition.status != ProcessingTaskStatus.FAILED:
            raise LiveWorkflowError(f"fault-injected processing did not fail: {transition}")
    finally:
        if fault_path.exists():
            fault_path.rename(asset_path)
    retry = api.post(
        f"/api/v1/operations/commands/processing-tasks/{process_id}/retry",
        {"reason": "真实全流程验证：原始资产恢复后重试"},
        f"live-processing-task-retry-{process_id}-v1",
    )
    transition = get_processing_runtime().dispatch(now=datetime.now(TIMEZONE))
    if transition is None or transition.status != ProcessingTaskStatus.SUCCESS:
        raise LiveWorkflowError(f"processing retry did not succeed: {transition}")
    with SyncSessionFactory() as session:
        process = session.get(ProcessingTask, process_id)
        if process is None or process.attempt_count != 2:
            raise LiveWorkflowError("processing retry attempt count is not 2")
    _progress(f"processing retry succeeded: process={process_id}, attempts=2")
    return {
        "sourceBatchId": str(batch_id),
        "processId": str(process_id),
        "attemptCount": 2,
        "commandId": retry["commandId"],
    }


def _run_collection_retries(
    api: OperationsApi,
    current_closed_date: date,
    historical_closed_date: date,
    *,
    api_name: str = "daily",
    idempotency_prefix: str = "live",
) -> dict[str, object]:
    automatic = api.post(
        "/api/v1/operations/commands/repairs",
        {
            "businessDate": current_closed_date.isoformat(),
            "apiNames": [api_name],
            "reason": "真实全流程验证：当日空结果自动退避",
        },
        f"{idempotency_prefix}-auto-retry-{current_closed_date:%Y%m%d}-v1",
    )
    automatic_batch_id = _batch_ids_from_command(automatic)[0]
    _drain_collection()
    automatic_task = _first_task(automatic_batch_id)
    wait_seconds = _wait_until_retry_due(automatic_task.task_id)
    _drain_collection()
    retried_task = _first_task(automatic_batch_id)
    if (
        retried_task.status != CollectionTaskStatus.RETRY_WAIT.value
        or retried_task.attempt_count != 2
    ):
        raise LiveWorkflowError(
            "automatic collection retry did not execute its second physical request"
        )
    api.post(
        f"/api/v1/operations/commands/collection-tasks/{retried_task.task_id}/cancel",
        {"reason": "真实全流程验证完成，取消后续等待"},
        f"{idempotency_prefix}-auto-retry-cancel-{retried_task.task_id}-v1",
    )
    _close_batches()

    failed = api.post(
        "/api/v1/operations/commands/repairs",
        {
            "businessDate": historical_closed_date.isoformat(),
            "apiNames": [api_name],
            "reason": "真实全流程验证：历史休市日失败任务",
        },
        f"{idempotency_prefix}-manual-retry-source-{historical_closed_date:%Y%m%d}-v1",
    )
    failed_batch_id = _batch_ids_from_command(failed)[0]
    _drain_collection()
    failed_task = _first_task(failed_batch_id)
    if failed_task.status != CollectionTaskStatus.FAILED.value:
        raise LiveWorkflowError(f"historical closed-day request did not fail: {failed_task.status}")
    _close_batches()
    manual_retry = api.post(
        f"/api/v1/operations/commands/collection-tasks/{failed_task.task_id}/retry",
        {"reason": "真实全流程验证：人工重试失败采集任务"},
        f"{idempotency_prefix}-manual-collection-retry-{failed_task.task_id}-v1",
    )
    retry_batch_id = _batch_ids_from_command(manual_retry)[0]
    retry_task = _first_task(retry_batch_id)
    if retry_task.task_id == failed_task.task_id:
        raise LiveWorkflowError("manual collection retry reused the terminal task")
    _drain_collection()
    retry_task = _first_task(retry_batch_id)
    if retry_task.status != CollectionTaskStatus.FAILED.value:
        raise LiveWorkflowError(f"manual retry fixture has unexpected status: {retry_task.status}")
    _close_batches()
    _progress(
        f"collection retries verified: automatic attempts=2, manual repair batch={retry_batch_id}"
    )
    return {
        "automatic": {
            "batchId": str(automatic_batch_id),
            "taskId": str(retried_task.task_id),
            "attemptCount": retried_task.attempt_count,
            "waitSeconds": round(wait_seconds, 3),
            "finalStatus": CollectionTaskStatus.CANCELLED.value,
        },
        "manual": {
            "sourceBatchId": str(failed_batch_id),
            "sourceTaskId": str(failed_task.task_id),
            "retryBatchId": str(retry_batch_id),
            "retryTaskId": str(retry_task.task_id),
            "sourceStatus": failed_task.status,
            "retryStatus": retry_task.status,
            "commandId": manual_retry["commandId"],
        },
    }


def _file_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise LiveWorkflowError(f"unsupported raw asset URI: {uri}")
    return Path(unquote(parsed.path))


def _verify_raw_assets(batch_ids: Sequence[UUID]) -> dict[str, int]:
    with SyncSessionFactory() as session:
        rows = list(
            session.execute(
                select(RawDataAsset, CollectionTask)
                .join(CollectionTask, CollectionTask.task_id == RawDataAsset.task_id)
                .where(CollectionTask.batch_id.in_(batch_ids))
            ).all()
        )
    total_rows = 0
    total_bytes = 0
    asset_store = get_raw_asset_store()
    for asset, task in rows:
        path = _file_uri_path(asset.storage_uri)
        if not path.is_file() or not asset.is_complete:
            raise LiveWorkflowError(f"raw asset missing or incomplete: {asset.asset_id}")
        if task.status not in SUCCESSFUL_COLLECTION_STATUSES:
            raise LiveWorkflowError(f"asset belongs to unsuccessful task: {task.task_id}")
        metadata = RawAssetMetadata(
            storage_uri=asset.storage_uri,
            content_hash=asset.content_hash,
            schema_fingerprint=asset.schema_fingerprint,
            row_count=asset.row_count,
            size_bytes=path.stat().st_size,
        )
        asset_store.verify(metadata)
        total_rows += asset.row_count
        total_bytes += metadata.size_bytes
    expected_tasks = sum(
        1 for row in _batch_task_rows(batch_ids) if row.status in SUCCESSFUL_COLLECTION_STATUSES
    )
    if len(rows) != expected_tasks:
        raise LiveWorkflowError(
            f"raw asset count mismatch: assets={len(rows)}, successfulTasks={expected_tasks}"
        )
    return {
        "assetCount": len(rows),
        "verifiedHashCount": len(rows),
        "rowCount": total_rows,
        "bytes": total_bytes,
    }


def _verify_business_data(trading_dates: Sequence[date]) -> dict[str, object]:
    with SyncSessionFactory() as session:
        stock_count = int(session.scalar(select(func.count()).select_from(Stock)) or 0)
        company_count = int(session.scalar(select(func.count()).select_from(StockCompany)) or 0)
        calendar_count = int(session.scalar(select(func.count()).select_from(TradeCalendar)) or 0)
        daily_counts: dict[str, dict[str, int]] = {}
        for business_date in trading_dates:
            counts = {
                "stockDaily": int(
                    session.scalar(
                        select(func.count())
                        .select_from(StockDaily)
                        .where(StockDaily.trade_date == business_date)
                    )
                    or 0
                ),
                "technical": int(
                    session.scalar(
                        select(func.count())
                        .select_from(StockTechnicalDaily)
                        .where(StockTechnicalDaily.trade_date == business_date)
                    )
                    or 0
                ),
                "moneyflow": int(
                    session.scalar(
                        select(func.count())
                        .select_from(StockMoneyflowDaily)
                        .where(StockMoneyflowDaily.trade_date == business_date)
                    )
                    or 0
                ),
                "suspend": int(
                    session.scalar(
                        select(func.count())
                        .select_from(StockSuspendDaily)
                        .where(StockSuspendDaily.trade_date == business_date)
                    )
                    or 0
                ),
            }
            release_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(DatasetRelease)
                    .where(
                        DatasetRelease.scope_type == "DATE",
                        DatasetRelease.scope_key == business_date.isoformat(),
                    )
                )
                or 0
            )
            if (
                counts["stockDaily"] <= 5_000
                or counts["technical"] < counts["stockDaily"]
                or counts["moneyflow"] <= 5_000
                or release_count != 5
            ):
                raise LiveWorkflowError(
                    f"business data validation failed for {business_date}: "
                    f"{counts}, releases={release_count}"
                )
            daily_counts[business_date.isoformat()] = {**counts, "releases": release_count}
    if stock_count <= 5_000 or company_count <= 4_000 or calendar_count not in {730, 732}:
        raise LiveWorkflowError(
            f"master data validation failed: stock={stock_count}, company={company_count}, "
            f"calendar={calendar_count}"
        )
    return {
        "stockCount": stock_count,
        "stockCompanyCount": company_count,
        "tradeCalendarCount": calendar_count,
        "daily": daily_counts,
    }


def _verify_operations_api(api: OperationsApi) -> dict[str, object]:
    paths = (
        "/api/v1/operations/overview",
        "/api/v1/operations/acquisition-batches?pageSize=200",
        "/api/v1/operations/processing-queue?pageSize=200",
        "/api/v1/operations/dependencies?pageSize=200",
        "/api/v1/operations/releases?pageSize=200",
        "/api/v1/operations/providers/tushare",
        "/api/v1/operations/runs?pageSize=200",
        "/api/v1/operations/alerts?pageSize=200",
        "/api/v1/system/resources",
    )
    responses = {path: api.get(path) for path in paths}
    provider = responses["/api/v1/operations/providers/tushare"]
    endpoints = provider.get("endpoints")
    if not isinstance(endpoints, list) or len(endpoints) < len(ALL_TUSHARE_API_SPECS):
        raise LiveWorkflowError("provider monitoring does not cover every configured endpoint")
    return {
        "pathCount": len(paths),
        "providerEndpointCount": len(endpoints),
        "generatedAt": responses["/api/v1/operations/overview"]["generatedAt"],
    }


def _database_counts() -> dict[str, int]:
    with SyncSessionFactory() as session:
        return {
            "collectionBatches": int(
                session.scalar(select(func.count()).select_from(CollectionBatch)) or 0
            ),
            "collectionTasks": int(
                session.scalar(select(func.count()).select_from(CollectionTask)) or 0
            ),
            "rawAssets": int(session.scalar(select(func.count()).select_from(RawDataAsset)) or 0),
            "processingTasks": int(
                session.scalar(select(func.count()).select_from(ProcessingTask)) or 0
            ),
            "providerRequests": int(
                session.scalar(select(func.count()).select_from(ProviderRequestLog)) or 0
            ),
            "datasetReleases": int(
                session.scalar(select(func.count()).select_from(DatasetRelease)) or 0
            ),
        }


def _historical_closed_date(start_date: date, end_date: date) -> date:
    with SyncSessionFactory() as session:
        value = session.scalar(
            select(TradeCalendar.cal_date)
            .where(
                TradeCalendar.exchange == "SSE",
                TradeCalendar.cal_date.between(start_date, end_date),
                TradeCalendar.is_open.is_(False),
                TradeCalendar.cal_date < datetime.now(TIMEZONE).date(),
            )
            .order_by(TradeCalendar.cal_date.desc())
            .limit(1)
        )
    if value is None:
        raise LiveWorkflowError("recent range contains no historical closed date")
    return value


def _assert_no_nonterminal_primary_tasks(batch_ids: Iterable[UUID]) -> None:
    values = tuple(batch_ids)
    with SyncSessionFactory() as session:
        count = int(
            session.scalar(
                select(func.count())
                .select_from(CollectionTask)
                .where(
                    CollectionTask.batch_id.in_(values),
                    CollectionTask.status.not_in(TERMINAL_TASK_STATUSES),
                )
            )
            or 0
        )
    if count:
        raise LiveWorkflowError(f"primary flow has {count} non-terminal collection tasks")


def run(start_date: date, end_date: date, report_path: Path) -> dict[str, object]:
    if (end_date - start_date).days != 13:
        raise LiveWorkflowError("validation range must be exactly 14 calendar days")
    database_name = _validate_environment()
    _ensure_partitions(start_date, end_date)
    api = OperationsApi()
    try:
        month_start = date(end_date.year, end_date.month, 1)
        calendar_batch = _plan_stage(
            batch_type=BatchType.MASTER,
            business_date=month_start,
            scheduled_at=datetime.combine(month_start, day_time(8, 20), TIMEZONE),
            specs=(TRADE_CAL_SPEC,),
            finalize=True,
        )
        _drain_collection()
        _close_batches()
        _assert_collection_success((calendar_batch,), "trade calendar")
        _plan_and_drain_processing((calendar_batch,), "trade calendar")

        stock_batch = _plan_stage(
            batch_type=BatchType.MASTER,
            business_date=month_start,
            scheduled_at=datetime.combine(month_start, day_time(8, 30), TIMEZONE),
            specs=MASTER_STOCK_SPECS,
            finalize=True,
        )
        _drain_collection()
        _close_batches()
        _assert_collection_success((stock_batch,), "stock master")
        _plan_and_drain_processing((stock_batch,), "stock master")

        trading_dates = _trading_dates(start_date, end_date)
        daily_date = trading_dates[-1]
        scheduled_at = datetime.combine(daily_date, day_time(8, 45), TIMEZONE)
        daily_batch: UUID | None = None
        for name, specs, finalize in (
            ("preopen", DAILY_PREOPEN_SPECS, False),
            ("close", DAILY_CLOSE_SPECS, False),
            ("late", DAILY_LATE_SPECS, False),
            ("final", DAILY_FINAL_SPECS, True),
        ):
            planned = _plan_stage(
                batch_type=BatchType.DAILY,
                business_date=daily_date,
                scheduled_at=scheduled_at,
                specs=specs,
                finalize=finalize,
            )
            if daily_batch is not None and planned != daily_batch:
                raise LiveWorkflowError("daily stages did not reuse the same batch")
            daily_batch = planned
            submitted = _drain_collection()
            _progress(f"daily {name}: dispatched={submitted}")
        if daily_batch is None:
            raise LiveWorkflowError("daily batch was not created")
        _close_batches()
        _assert_collection_success((daily_batch,), "daily staged flow")

        backfill_end = daily_date - timedelta(days=1)
        backfill = api.post(
            "/api/v1/operations/commands/backfills",
            {
                "startDate": start_date.isoformat(),
                "endDate": backfill_end.isoformat(),
                "apiNames": list(DAILY_API_NAMES),
                "reason": "真实全流程验证：最近两周历史数据回补",
            },
            f"live-backfill-{start_date:%Y%m%d}-{backfill_end:%Y%m%d}-v1",
        )
        backfill_batches = _batch_ids_from_command(backfill)
        expected_backfill_dates = tuple(item for item in trading_dates if item < daily_date)
        if len(backfill_batches) != len(expected_backfill_dates):
            raise LiveWorkflowError(
                f"backfill batch count mismatch: {len(backfill_batches)} "
                f"!= {len(expected_backfill_dates)}"
            )
        _progress(
            f"backfill command accepted: dates={len(expected_backfill_dates)}, "
            f"tasks={len(expected_backfill_dates) * len(DAILY_SPECS)}"
        )
        _drain_collection()
        _close_batches()
        _assert_collection_success(backfill_batches, "historical backfill")

        primary_daily_batches = (*backfill_batches, daily_batch)
        _plan_and_drain_processing(primary_daily_batches, "recent two-week daily data")
        _assert_no_nonterminal_primary_tasks(primary_daily_batches)
        business = _verify_business_data(trading_dates)

        processing_retry = _run_processing_retry(api, daily_date)
        retries = _run_collection_retries(
            api,
            current_closed_date=end_date,
            historical_closed_date=_historical_closed_date(start_date, end_date),
        )
        raw = _verify_raw_assets(
            (
                calendar_batch,
                stock_batch,
                *primary_daily_batches,
                UUID(str(processing_retry["sourceBatchId"])),
            )
        )
        operations = _verify_operations_api(api)
        report = {
            "generatedAt": datetime.now(UTC).isoformat(),
            "database": database_name,
            "dateRange": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "tradingDates": [item.isoformat() for item in trading_dates],
                "dailyDate": daily_date.isoformat(),
                "backfillDates": [item.isoformat() for item in expected_backfill_dates],
            },
            "batches": {
                "tradeCalendar": str(calendar_batch),
                "stockMaster": str(stock_batch),
                "daily": str(daily_batch),
                "backfill": [str(item) for item in backfill_batches],
            },
            "rawAssets": raw,
            "businessData": business,
            "processingRetry": processing_retry,
            "collectionRetries": retries,
            "operationsApi": operations,
            "databaseCounts": _database_counts(),
            "passed": True,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _progress(f"live workflow validation PASS: {report_path}")
        return report
    finally:
        api.close()


def _parse_args() -> argparse.Namespace:
    today = datetime.now(TIMEZONE).date()
    parser = argparse.ArgumentParser(
        description="Validate recent workflows with real Tushare calls"
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=today - timedelta(days=13))
    parser.add_argument("--end-date", type=date.fromisoformat, default=today)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(
            os.environ.get(
                "LIVE_VALIDATION_REPORT",
                "dist/live-validation/recent-workflows.json",
            )
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    run(args.start_date, args.end_date, args.report.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
