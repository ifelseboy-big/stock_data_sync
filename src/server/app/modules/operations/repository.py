from datetime import date, datetime
from typing import Any

from sqlalchemy import String, case, func, literal, or_, select, union_all
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.acquisition.models import (
    BatchStatus,
    CollectionBatch,
    CollectionTask,
    CollectionTaskStatus,
    RawDataAsset,
)
from app.modules.operations.models import ProviderRequestLog
from app.modules.processing.models import (
    DatasetRelease,
    DependencyStatus,
    ProcessingDependency,
    ProcessingTask,
    ProcessingTaskStatus,
)

COLLECTION_SUCCESS = (
    CollectionTaskStatus.SUCCESS.value,
    CollectionTaskStatus.EMPTY_VALID.value,
)
COLLECTION_FAILED = (
    CollectionTaskStatus.FAILED.value,
    CollectionTaskStatus.SKIPPED.value,
    CollectionTaskStatus.CANCELLED.value,
)
COLLECTION_TERMINAL = (*COLLECTION_SUCCESS, *COLLECTION_FAILED)
PROCESSING_SUCCESS = (ProcessingTaskStatus.SUCCESS.value,)
PROCESSING_FAILED = (
    ProcessingTaskStatus.FAILED.value,
    ProcessingTaskStatus.SKIPPED.value,
    ProcessingTaskStatus.CANCELLED.value,
)
PROCESSING_TERMINAL = (*PROCESSING_SUCCESS, *PROCESSING_FAILED)


class OperationsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def overview_counts(self, *, day_start: datetime) -> dict[str, Any]:
        collecting = await self._session.scalar(
            select(func.count())
            .select_from(CollectionBatch)
            .where(CollectionBatch.status == BatchStatus.RUNNING.value)
        )
        processing = await self._session.scalar(
            select(func.count())
            .select_from(ProcessingTask)
            .where(ProcessingTask.status == ProcessingTaskStatus.RUNNING.value)
        )
        blocked = await self._session.scalar(
            select(func.count())
            .select_from(ProcessingTask)
            .where(ProcessingTask.status == ProcessingTaskStatus.BLOCKED.value)
        )
        collection_success, collection_terminal = (
            await self._session.execute(
                select(
                    func.count().filter(CollectionTask.status.in_(COLLECTION_SUCCESS)),
                    func.count().filter(CollectionTask.status.in_(COLLECTION_TERMINAL)),
                ).where(CollectionTask.finished_at >= day_start)
            )
        ).one()
        processing_success, processing_terminal = (
            await self._session.execute(
                select(
                    func.count().filter(ProcessingTask.status.in_(PROCESSING_SUCCESS)),
                    func.count().filter(ProcessingTask.status.in_(PROCESSING_TERMINAL)),
                ).where(ProcessingTask.finished_at >= day_start)
            )
        ).one()
        provider_success, provider_total, provider_p95 = (
            await self._session.execute(
                select(
                    func.count().filter(ProviderRequestLog.status == "SUCCESS"),
                    func.count(),
                    func.percentile_cont(0.95).within_group(ProviderRequestLog.duration_ms),
                ).where(ProviderRequestLog.requested_at >= day_start)
            )
        ).one()
        return {
            "collecting_batch_count": int(collecting or 0),
            "processing_task_count": int(processing or 0),
            "blocked_task_count": int(blocked or 0),
            "task_success": int(collection_success or 0) + int(processing_success or 0),
            "task_terminal": int(collection_terminal or 0) + int(processing_terminal or 0),
            "provider_success": int(provider_success or 0),
            "provider_total": int(provider_total or 0),
            "provider_p95": provider_p95,
        }

    async def quota_counts(self, *, window_start: datetime) -> dict[str, int]:
        used, delayed = (
            await self._session.execute(
                select(
                    func.count(),
                    func.count().filter(ProviderRequestLog.rate_limit_wait_ms > 0),
                ).where(
                    ProviderRequestLog.provider == "tushare",
                    ProviderRequestLog.requested_at >= window_start,
                )
            )
        ).one()
        return {"used": int(used or 0), "delayed": int(delayed or 0)}

    async def acquisition_batches(
        self,
        *,
        since: datetime,
        status: str | None,
        business_date: date | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        aggregate = (
            select(
                CollectionBatch.batch_id.label("batch_id"),
                CollectionBatch.batch_type.label("batch_type"),
                CollectionBatch.business_date.label("business_date"),
                CollectionBatch.scheduled_at.label("scheduled_at"),
                CollectionBatch.status.label("batch_status"),
                CollectionBatch.started_at.label("started_at"),
                CollectionBatch.closed_at.label("closed_at"),
                func.count(CollectionTask.task_id).label("task_count"),
                func.count(CollectionTask.task_id)
                .filter(CollectionTask.status.in_(COLLECTION_SUCCESS))
                .label("success_count"),
                func.count(CollectionTask.task_id)
                .filter(CollectionTask.status.in_(COLLECTION_FAILED))
                .label("failed_count"),
                func.count(CollectionTask.task_id)
                .filter(CollectionTask.status == CollectionTaskStatus.RETRY_WAIT.value)
                .label("retry_count"),
            )
            .outerjoin(CollectionTask, CollectionTask.batch_id == CollectionBatch.batch_id)
            .where(CollectionBatch.scheduled_at >= since)
            .group_by(CollectionBatch.batch_id)
        )
        if business_date is not None:
            aggregate = aggregate.where(CollectionBatch.business_date == business_date)
        batch_rows = aggregate.subquery()
        filtered = select(batch_rows)
        status_condition = _batch_status_condition(batch_rows, status)
        if status_condition is not None:
            filtered = filtered.where(status_condition)
        total = await self._session.scalar(select(func.count()).select_from(filtered.subquery()))
        rows = (
            await self._session.execute(
                filtered.order_by(batch_rows.c.scheduled_at.desc()).offset(offset).limit(limit)
            )
        ).mappings()
        return [dict(row) for row in rows], int(total or 0)

    async def processing_queue(
        self,
        *,
        status: str | None,
        dataset_name: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        active_statuses = (
            ProcessingTaskStatus.WAITING_DEPENDENCY.value,
            ProcessingTaskStatus.QUEUED.value,
            ProcessingTaskStatus.RUNNING.value,
            ProcessingTaskStatus.RETRY_WAIT.value,
            ProcessingTaskStatus.BLOCKED.value,
        )
        statement = (
            select(
                ProcessingTask.process_id,
                ProcessingTask.source_batch_id,
                ProcessingTask.output_dataset,
                ProcessingTask.business_date,
                ProcessingTask.status,
                ProcessingTask.priority,
                ProcessingTask.queued_at,
                ProcessingTask.started_at,
                ProcessingTask.finished_at,
                ProcessingTask.next_retry_at,
                ProcessingTask.error_message,
                CollectionBatch.batch_type,
                func.count(ProcessingDependency.dependency_name).label("dependency_count"),
                func.count(ProcessingDependency.dependency_name)
                .filter(ProcessingDependency.status == DependencyStatus.READY.value)
                .label("ready_dependency_count"),
            )
            .join(
                CollectionBatch,
                CollectionBatch.batch_id == ProcessingTask.source_batch_id,
            )
            .outerjoin(
                ProcessingDependency,
                ProcessingDependency.process_id == ProcessingTask.process_id,
            )
            .where(ProcessingTask.status.in_(active_statuses))
            .group_by(ProcessingTask.process_id, CollectionBatch.batch_type)
        )
        if dataset_name:
            statement = statement.where(ProcessingTask.output_dataset == dataset_name)
        status_values = _processing_status_values(status)
        if status_values is not None:
            statement = statement.where(ProcessingTask.status.in_(status_values))
        queue_rows = statement.subquery()
        total = await self._session.scalar(select(func.count()).select_from(queue_rows))
        rows = (
            await self._session.execute(
                select(queue_rows)
                .order_by(
                    case(
                        (queue_rows.c.status == ProcessingTaskStatus.RUNNING.value, 0),
                        else_=1,
                    ),
                    queue_rows.c.priority,
                    queue_rows.c.business_date.asc().nullsfirst(),
                    queue_rows.c.queued_at.asc().nullsfirst(),
                    queue_rows.c.process_id,
                )
                .offset(offset)
                .limit(limit)
            )
        ).mappings()
        return [dict(row) for row in rows], int(total or 0)

    async def dependencies(
        self,
        *,
        since: datetime,
        status: str | None,
        query: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        asset_task = aliased(CollectionTask)
        release_task = aliased(ProcessingTask)
        statement = (
            select(
                ProcessingDependency.process_id,
                ProcessingDependency.dependency_type,
                ProcessingDependency.dependency_name,
                ProcessingDependency.dependency_scope_key,
                ProcessingDependency.status,
                ProcessingDependency.blocked_reason,
                ProcessingTask.output_dataset,
                ProcessingTask.source_batch_id,
                ProcessingTask.business_date,
                RawDataAsset.business_date.label("asset_business_date"),
                asset_task.batch_id.label("asset_batch_id"),
                release_task.source_batch_id.label("release_batch_id"),
            )
            .join(
                ProcessingTask,
                ProcessingTask.process_id == ProcessingDependency.process_id,
            )
            .join(
                CollectionBatch,
                CollectionBatch.batch_id == ProcessingTask.source_batch_id,
            )
            .outerjoin(
                RawDataAsset,
                RawDataAsset.asset_id == ProcessingDependency.resolved_asset_id,
            )
            .outerjoin(asset_task, asset_task.task_id == RawDataAsset.task_id)
            .outerjoin(
                release_task,
                release_task.process_id == ProcessingDependency.resolved_release_process_id,
            )
            .where(CollectionBatch.scheduled_at >= since)
        )
        dependency_values = _dependency_status_values(status)
        if dependency_values is not None:
            statement = statement.where(ProcessingDependency.status.in_(dependency_values))
        if query:
            pattern = f"%{query}%"
            statement = statement.where(
                or_(
                    ProcessingTask.output_dataset.ilike(pattern),
                    ProcessingDependency.dependency_name.ilike(pattern),
                    sql_cast(ProcessingTask.source_batch_id, String).ilike(pattern),
                )
            )
        total = await self._session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (
            await self._session.execute(
                statement.order_by(
                    CollectionBatch.scheduled_at.desc(),
                    ProcessingTask.output_dataset,
                    ProcessingDependency.dependency_name,
                    ProcessingDependency.dependency_scope_key,
                )
                .offset(offset)
                .limit(limit)
            )
        ).mappings()
        return [dict(row) for row in rows], int(total or 0)

    async def releases(
        self,
        *,
        dataset_name: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        statement = select(
            DatasetRelease.dataset_name,
            DatasetRelease.scope_type,
            DatasetRelease.scope_key,
            DatasetRelease.business_date,
            DatasetRelease.version_id,
            DatasetRelease.process_id,
            DatasetRelease.row_count,
            DatasetRelease.published_at,
            ProcessingTask.process_type,
        ).join(ProcessingTask, ProcessingTask.process_id == DatasetRelease.process_id)
        if dataset_name:
            statement = statement.where(DatasetRelease.dataset_name == dataset_name)
        total = await self._session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (
            await self._session.execute(
                statement.order_by(DatasetRelease.published_at.desc()).offset(offset).limit(limit)
            )
        ).mappings()
        return [dict(row) for row in rows], int(total or 0)

    async def provider_endpoints(self, *, day_start: datetime) -> list[dict[str, Any]]:
        rows = (
            await self._session.execute(
                select(
                    ProviderRequestLog.endpoint,
                    func.count().label("request_count"),
                    func.count()
                    .filter(ProviderRequestLog.status == "SUCCESS")
                    .label("success_count"),
                    func.percentile_cont(0.5)
                    .within_group(ProviderRequestLog.duration_ms)
                    .label("p50"),
                    func.percentile_cont(0.95)
                    .within_group(ProviderRequestLog.duration_ms)
                    .label("p95"),
                    func.count()
                    .filter(ProviderRequestLog.rate_limit_wait_ms > 0)
                    .label("throttled_count"),
                    func.count().filter(ProviderRequestLog.row_count == 0).label("empty_count"),
                    func.max(ProviderRequestLog.requested_at).label("last_requested_at"),
                )
                .where(
                    ProviderRequestLog.provider == "tushare",
                    ProviderRequestLog.requested_at >= day_start,
                )
                .group_by(ProviderRequestLog.endpoint)
                .order_by(ProviderRequestLog.endpoint)
            )
        ).mappings()
        return [dict(row) for row in rows]

    async def run_records(
        self,
        *,
        since: datetime,
        run_type: str | None,
        status: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        collection = select(
            CollectionTask.task_id.label("id"),
            literal("acquisition").label("run_type"),
            CollectionTask.api_name.label("task_name"),
            CollectionTask.batch_id.label("batch_id"),
            CollectionBatch.business_date.label("business_date"),
            CollectionTask.status.label("raw_status"),
            CollectionTask.attempt_count.label("attempt"),
            CollectionTask.started_at.label("started_at"),
            CollectionTask.finished_at.label("finished_at"),
            CollectionTask.error_message.label("error_message"),
            CollectionBatch.scheduled_at.label("sort_time"),
        ).join(CollectionBatch, CollectionBatch.batch_id == CollectionTask.batch_id)
        processing = select(
            ProcessingTask.process_id.label("id"),
            literal("processing").label("run_type"),
            ProcessingTask.output_dataset.label("task_name"),
            ProcessingTask.source_batch_id.label("batch_id"),
            ProcessingTask.business_date.label("business_date"),
            ProcessingTask.status.label("raw_status"),
            ProcessingTask.attempt_count.label("attempt"),
            ProcessingTask.started_at.label("started_at"),
            ProcessingTask.finished_at.label("finished_at"),
            ProcessingTask.error_message.label("error_message"),
            CollectionBatch.scheduled_at.label("sort_time"),
        ).join(CollectionBatch, CollectionBatch.batch_id == ProcessingTask.source_batch_id)
        statements = []
        if run_type in (None, "acquisition"):
            statements.append(collection.where(CollectionBatch.scheduled_at >= since))
        if run_type in (None, "processing"):
            statements.append(processing.where(CollectionBatch.scheduled_at >= since))
        combined = union_all(*statements).subquery()
        filtered = select(combined)
        status_condition = _run_status_condition(combined, status)
        if status_condition is not None:
            filtered = filtered.where(status_condition)
        total = await self._session.scalar(select(func.count()).select_from(filtered.subquery()))
        rows = (
            await self._session.execute(
                filtered.order_by(combined.c.sort_time.desc(), combined.c.id)
                .offset(offset)
                .limit(limit)
            )
        ).mappings()
        return [dict(row) for row in rows], int(total or 0)

    async def alert_rows(
        self,
        *,
        since: datetime,
        source: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        collection = select(
            CollectionTask.task_id.label("id"),
            literal("acquisition").label("source"),
            CollectionTask.api_name.label("task_name"),
            CollectionTask.status.label("status"),
            CollectionTask.error_code.label("error_code"),
            CollectionTask.error_message.label("error_message"),
            CollectionTask.finished_at.label("occurred_at"),
        ).where(
            CollectionTask.status.in_(COLLECTION_FAILED),
            CollectionTask.finished_at >= since,
        )
        processing = select(
            ProcessingTask.process_id.label("id"),
            literal("processing").label("source"),
            ProcessingTask.output_dataset.label("task_name"),
            ProcessingTask.status.label("status"),
            literal(None).label("error_code"),
            ProcessingTask.error_message.label("error_message"),
            func.coalesce(
                ProcessingTask.finished_at,
                ProcessingTask.started_at,
                ProcessingTask.queued_at,
            ).label("occurred_at"),
        ).where(
            ProcessingTask.status.in_(
                (ProcessingTaskStatus.FAILED.value, ProcessingTaskStatus.BLOCKED.value)
            ),
            or_(
                ProcessingTask.finished_at >= since,
                ProcessingTask.started_at >= since,
                ProcessingTask.queued_at >= since,
            ),
        )
        combined = union_all(collection, processing).subquery()
        statement = select(combined)
        if source:
            statement = statement.where(combined.c.source == source)
        total = await self._session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (
            await self._session.execute(
                statement.order_by(combined.c.occurred_at.desc().nullslast())
                .offset(offset)
                .limit(limit)
            )
        ).mappings()
        return [dict(row) for row in rows], int(total or 0)


def _batch_status_condition(batch_rows: Any, status: str | None) -> Any:
    if status is None:
        return None
    if status == "pending":
        return batch_rows.c.batch_status == BatchStatus.PENDING.value
    if status == "running":
        return batch_rows.c.batch_status == BatchStatus.RUNNING.value
    if status == "waiting_retry":
        return batch_rows.c.retry_count > 0
    if status in {"closed", "succeeded"}:
        return (batch_rows.c.batch_status == BatchStatus.CLOSED.value) & (
            batch_rows.c.failed_count == 0
        )
    if status == "partial_failed":
        return (batch_rows.c.batch_status == BatchStatus.CLOSED.value) & (
            batch_rows.c.failed_count > 0
        )
    if status == "failed":
        return batch_rows.c.batch_status == BatchStatus.CANCELLED.value
    return literal(False)


def _run_status_condition(rows: Any, status: str | None) -> Any:
    if status is None:
        return None
    values = {
        "pending": (
            CollectionTaskStatus.PENDING.value,
            ProcessingTaskStatus.WAITING_DEPENDENCY.value,
            ProcessingTaskStatus.QUEUED.value,
        ),
        "running": (
            CollectionTaskStatus.RUNNING.value,
            ProcessingTaskStatus.RUNNING.value,
        ),
        "waiting_retry": (
            CollectionTaskStatus.RETRY_WAIT.value,
            ProcessingTaskStatus.RETRY_WAIT.value,
        ),
        "succeeded": (*COLLECTION_SUCCESS, *PROCESSING_SUCCESS),
        "failed": (*COLLECTION_FAILED, *PROCESSING_FAILED),
        "blocked": (ProcessingTaskStatus.BLOCKED.value,),
    }.get(status)
    return rows.c.raw_status.in_(values) if values else literal(False)


def _processing_status_values(status: str | None) -> tuple[str, ...] | None:
    if status is None:
        return None
    return {
        "pending": (
            ProcessingTaskStatus.WAITING_DEPENDENCY.value,
            ProcessingTaskStatus.QUEUED.value,
        ),
        "running": (ProcessingTaskStatus.RUNNING.value,),
        "waiting_retry": (ProcessingTaskStatus.RETRY_WAIT.value,),
        "blocked": (ProcessingTaskStatus.BLOCKED.value,),
    }.get(status, ("__NO_MATCH__",))


def _dependency_status_values(status: str | None) -> tuple[str, ...] | None:
    if status is None:
        return None
    return {
        "pending": (DependencyStatus.WAITING.value,),
        "succeeded": (DependencyStatus.READY.value,),
        "blocked": (DependencyStatus.MISSING.value, DependencyStatus.FAILED.value),
    }.get(status, ("__NO_MATCH__",))
