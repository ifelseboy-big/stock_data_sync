from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import MetaData, String, Table, case, func, inspect, literal, or_, select, union_all
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.catalog.datasets import ALL_DATASET_SPECS
from app.catalog.specs import ReleaseScope
from app.modules.acquisition.models import (
    BatchStatus,
    CollectionBatch,
    CollectionTask,
    CollectionTaskStatus,
)
from app.modules.operations.models import (
    ProviderRequestLog,
    ScheduledJobControl,
    ScheduledJobExecution,
)
from app.modules.processing.models import (
    DatasetRelease,
    DependencyStatus,
    ProcessingDependency,
    ProcessingTask,
    ProcessingTaskStatus,
)
from app.modules.stocks.models import TradeCalendar

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
DATE_SCOPED_DATASETS = tuple(
    spec.dataset_name for spec in ALL_DATASET_SPECS if spec.release_scope == ReleaseScope.DATE
)


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
        readiness: str,
        query: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        searched_dependency = aliased(ProcessingDependency)
        recovered_release = aliased(DatasetRelease)
        statement = (
            select(
                ProcessingDependency.process_id,
                ProcessingTask.output_dataset,
                ProcessingTask.source_batch_id,
                ProcessingTask.business_date,
                ProcessingTask.status.label("processing_status"),
                ProcessingTask.error_message,
                CollectionBatch.scheduled_at,
                select(literal(1))
                .where(
                    recovered_release.dataset_name == ProcessingTask.output_dataset,
                    or_(
                        ProcessingTask.output_dataset.not_in(DATE_SCOPED_DATASETS),
                        recovered_release.business_date.is_not_distinct_from(
                            ProcessingTask.business_date
                        ),
                    ),
                    recovered_release.published_at > CollectionBatch.scheduled_at,
                )
                .exists()
                .label("recovered"),
                func.count(ProcessingDependency.dependency_scope_key).label("dependency_count"),
                func.count(ProcessingDependency.dependency_scope_key)
                .filter(ProcessingDependency.status == DependencyStatus.READY.value)
                .label("ready_dependency_count"),
                func.count(ProcessingDependency.dependency_scope_key)
                .filter(ProcessingDependency.status == DependencyStatus.WAITING.value)
                .label("waiting_dependency_count"),
                func.count(ProcessingDependency.dependency_scope_key)
                .filter(
                    ProcessingDependency.status.in_(
                        (DependencyStatus.MISSING.value, DependencyStatus.FAILED.value)
                    )
                )
                .label("blocked_dependency_count"),
            )
            .join(
                ProcessingTask,
                ProcessingTask.process_id == ProcessingDependency.process_id,
            )
            .join(
                CollectionBatch,
                CollectionBatch.batch_id == ProcessingTask.source_batch_id,
            )
            .where(CollectionBatch.scheduled_at >= since)
            .group_by(
                ProcessingDependency.process_id,
                ProcessingTask.process_id,
                CollectionBatch.batch_id,
            )
        )
        if query:
            pattern = f"%{query}%"
            statement = statement.where(
                or_(
                    ProcessingTask.output_dataset.ilike(pattern),
                    sql_cast(ProcessingTask.source_batch_id, String).ilike(pattern),
                    select(literal(1))
                    .where(
                        searched_dependency.process_id == ProcessingTask.process_id,
                        or_(
                            searched_dependency.dependency_name.ilike(pattern),
                            searched_dependency.dependency_scope_key.ilike(pattern),
                        ),
                    )
                    .exists(),
                )
            )
        summary = statement.subquery()
        filtered = select(summary)
        if readiness == "attention":
            filtered = filtered.where(
                summary.c.ready_dependency_count < summary.c.dependency_count,
                summary.c.recovered.is_(False),
                summary.c.processing_status.not_in(
                    (
                        ProcessingTaskStatus.SKIPPED.value,
                        ProcessingTaskStatus.CANCELLED.value,
                    )
                ),
            )
        elif readiness == "waiting":
            filtered = filtered.where(
                summary.c.waiting_dependency_count > 0,
                summary.c.blocked_dependency_count == 0,
                summary.c.recovered.is_(False),
            )
        elif readiness == "blocked":
            filtered = filtered.where(
                summary.c.blocked_dependency_count > 0,
                summary.c.recovered.is_(False),
                summary.c.processing_status.not_in(
                    (
                        ProcessingTaskStatus.SKIPPED.value,
                        ProcessingTaskStatus.CANCELLED.value,
                    )
                ),
            )
        elif readiness == "ready":
            filtered = filtered.where(
                summary.c.dependency_count > 0,
                summary.c.ready_dependency_count == summary.c.dependency_count,
            )
        total = await self._session.scalar(select(func.count()).select_from(filtered.subquery()))
        rows = (
            await self._session.execute(
                filtered.order_by(
                    case((summary.c.blocked_dependency_count > 0, 0), else_=1),
                    case((summary.c.waiting_dependency_count > 0, 0), else_=1),
                    summary.c.scheduled_at.desc(),
                    summary.c.output_dataset,
                    summary.c.process_id,
                )
                .offset(offset)
                .limit(limit)
            )
        ).mappings()
        return [dict(row) for row in rows], int(total or 0)

    async def dependency_source_summaries(
        self,
        *,
        process_ids: tuple[UUID, ...],
    ) -> list[dict[str, Any]]:
        if not process_ids:
            return []
        rows = (
            await self._session.execute(
                select(
                    ProcessingDependency.process_id,
                    ProcessingDependency.dependency_type,
                    ProcessingDependency.dependency_name,
                    func.count(ProcessingDependency.dependency_scope_key).label("required_count"),
                    func.count(ProcessingDependency.dependency_scope_key)
                    .filter(ProcessingDependency.status == DependencyStatus.READY.value)
                    .label("ready_count"),
                    func.count(ProcessingDependency.dependency_scope_key)
                    .filter(ProcessingDependency.status == DependencyStatus.WAITING.value)
                    .label("waiting_count"),
                    func.count(ProcessingDependency.dependency_scope_key)
                    .filter(
                        ProcessingDependency.status.in_(
                            (DependencyStatus.MISSING.value, DependencyStatus.FAILED.value)
                        )
                    )
                    .label("blocked_count"),
                    func.max(ProcessingDependency.blocked_reason).label("blocked_reason"),
                )
                .where(ProcessingDependency.process_id.in_(process_ids))
                .group_by(
                    ProcessingDependency.process_id,
                    ProcessingDependency.dependency_type,
                    ProcessingDependency.dependency_name,
                )
                .order_by(
                    ProcessingDependency.process_id,
                    ProcessingDependency.dependency_type,
                    ProcessingDependency.dependency_name,
                )
            )
        ).mappings()
        return [dict(row) for row in rows]

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

    async def release_coverage(
        self,
        *,
        start_date: date | None,
        end_date: date,
        day_count: int | None,
        dataset_names: tuple[str, ...],
    ) -> list[tuple[date, set[str]]]:
        trading_date_query = (
            select(TradeCalendar.cal_date)
            .where(
                TradeCalendar.exchange == "SSE",
                TradeCalendar.is_open.is_(True),
                TradeCalendar.cal_date <= end_date,
            )
            .order_by(TradeCalendar.cal_date.desc())
        )
        if start_date is not None:
            trading_date_query = trading_date_query.where(TradeCalendar.cal_date >= start_date)
        if day_count is not None:
            trading_date_query = trading_date_query.limit(day_count)
        trading_dates = tuple(await self._session.scalars(trading_date_query))
        if not trading_dates:
            return []
        rows = (
            await self._session.execute(
                select(DatasetRelease.business_date, DatasetRelease.dataset_name).where(
                    DatasetRelease.business_date.in_(trading_dates),
                    DatasetRelease.dataset_name.in_(dataset_names),
                )
            )
        ).all()
        published_by_date: dict[date, set[str]] = {item: set() for item in trading_dates}
        for business_date, dataset_name in rows:
            if business_date is not None:
                published_by_date[business_date].add(str(dataset_name))
        return [(item, published_by_date[item]) for item in trading_dates]

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
        batch_id: UUID | None,
        unresolved_only: bool,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        recovered_collection = aliased(CollectionTask)
        recovered_release = aliased(DatasetRelease)
        collection = select(
            CollectionTask.task_id.label("id"),
            literal("acquisition").label("run_type"),
            CollectionTask.api_name.label("task_name"),
            CollectionTask.scope_key.label("scope_key"),
            CollectionTask.batch_id.label("batch_id"),
            CollectionBatch.business_date.label("business_date"),
            CollectionTask.status.label("raw_status"),
            CollectionTask.attempt_count.label("attempt"),
            CollectionTask.started_at.label("started_at"),
            CollectionTask.finished_at.label("finished_at"),
            CollectionTask.error_message.label("error_message"),
            CollectionBatch.scheduled_at.label("sort_time"),
            select(literal(1))
            .where(
                recovered_collection.api_name == CollectionTask.api_name,
                recovered_collection.scope_key == CollectionTask.scope_key,
                recovered_collection.status.in_(COLLECTION_SUCCESS),
                recovered_collection.finished_at > CollectionTask.finished_at,
            )
            .exists()
            .label("recovered"),
        ).join(CollectionBatch, CollectionBatch.batch_id == CollectionTask.batch_id)
        processing = select(
            ProcessingTask.process_id.label("id"),
            literal("processing").label("run_type"),
            ProcessingTask.output_dataset.label("task_name"),
            literal(None).cast(String).label("scope_key"),
            ProcessingTask.source_batch_id.label("batch_id"),
            ProcessingTask.business_date.label("business_date"),
            ProcessingTask.status.label("raw_status"),
            ProcessingTask.attempt_count.label("attempt"),
            ProcessingTask.started_at.label("started_at"),
            ProcessingTask.finished_at.label("finished_at"),
            ProcessingTask.error_message.label("error_message"),
            CollectionBatch.scheduled_at.label("sort_time"),
            select(literal(1))
            .where(
                recovered_release.dataset_name == ProcessingTask.output_dataset,
                or_(
                    ProcessingTask.output_dataset.not_in(DATE_SCOPED_DATASETS),
                    recovered_release.business_date.is_not_distinct_from(
                        ProcessingTask.business_date
                    ),
                ),
                recovered_release.published_at > CollectionBatch.scheduled_at,
            )
            .exists()
            .label("recovered"),
        ).join(CollectionBatch, CollectionBatch.batch_id == ProcessingTask.source_batch_id)
        statements = []
        if run_type in (None, "acquisition"):
            statements.append(collection.where(CollectionBatch.scheduled_at >= since))
        if run_type in (None, "processing"):
            statements.append(processing.where(CollectionBatch.scheduled_at >= since))
        combined = union_all(*statements).subquery()
        filtered = select(combined)
        if batch_id is not None:
            filtered = filtered.where(combined.c.batch_id == batch_id)
        if unresolved_only:
            filtered = filtered.where(
                combined.c.recovered.is_(False),
                or_(combined.c.run_type == "acquisition", combined.c.attempt > 0),
            )
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

    async def scheduled_job_controls(self) -> dict[str, bool]:
        rows = (
            await self._session.execute(
                select(ScheduledJobControl.job_id, ScheduledJobControl.enabled)
            )
        ).all()
        return {str(job_id): bool(enabled) for job_id, enabled in rows}

    async def latest_scheduled_job_executions(self) -> dict[str, dict[str, Any]]:
        ranked = select(
            ScheduledJobExecution,
            func.row_number()
            .over(
                partition_by=ScheduledJobExecution.job_id,
                order_by=ScheduledJobExecution.created_at.desc(),
            )
            .label("row_number"),
        ).subquery()
        rows = (
            await self._session.execute(select(ranked).where(ranked.c.row_number == 1))
        ).mappings()
        return {str(row["job_id"]): dict(row) for row in rows}

    async def scheduled_job_next_runs(self, table_name: str) -> dict[str, datetime]:
        connection = await self._session.connection()

        def load(sync_connection: Any) -> list[tuple[str, float | None]]:
            if not inspect(sync_connection).has_table(table_name):
                return []
            table = Table(table_name, MetaData(), autoload_with=sync_connection)
            return list(sync_connection.execute(select(table.c.id, table.c.next_run_time)).all())

        rows = await connection.run_sync(load)
        return {
            str(job_id): datetime.fromtimestamp(float(next_run_time), UTC)
            for job_id, next_run_time in rows
            if next_run_time is not None
        }

    async def scheduled_job_executions(
        self,
        *,
        job_id: str | None,
        status: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        statement = select(ScheduledJobExecution)
        if job_id:
            statement = statement.where(ScheduledJobExecution.job_id == job_id)
        if status:
            statement = statement.where(ScheduledJobExecution.status == status.upper())
        total = await self._session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (
            await self._session.execute(
                statement.order_by(
                    ScheduledJobExecution.created_at.desc(),
                    ScheduledJobExecution.execution_id,
                )
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
        return [
            {
                "execution_id": row.execution_id,
                "job_id": row.job_id,
                "trigger_type": row.trigger_type,
                "status": row.status,
                "requested_by": row.requested_by,
                "reason": row.reason,
                "scheduled_at": row.scheduled_at,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "duration_ms": row.duration_ms,
                "error_message": row.error_message,
            }
            for row in rows
        ], int(total or 0)

    async def alert_rows(
        self,
        *,
        since: datetime,
        source: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        recovered_collection = aliased(CollectionTask)
        recovered_processing = aliased(ProcessingTask)
        recovered_scheduler = aliased(ScheduledJobExecution)
        processing_occurred_at = func.coalesce(
            ProcessingTask.finished_at,
            ProcessingTask.started_at,
            ProcessingTask.queued_at,
            CollectionBatch.scheduled_at,
        )
        processing_warning = (
            (ProcessingTask.status == ProcessingTaskStatus.SUCCESS.value)
            & ProcessingTask.warning_message.is_not(None)
        )
        scheduler_occurred_at = func.coalesce(
            ScheduledJobExecution.finished_at,
            ScheduledJobExecution.started_at,
            ScheduledJobExecution.created_at,
        )
        collection = select(
            CollectionTask.task_id.label("id"),
            literal("acquisition").label("source"),
            CollectionTask.api_name.label("task_name"),
            CollectionTask.status.label("status"),
            CollectionTask.error_code.label("error_code"),
            CollectionTask.error_message.label("error_message"),
            CollectionTask.finished_at.label("occurred_at"),
        ).where(
            CollectionTask.status == CollectionTaskStatus.FAILED.value,
            CollectionTask.finished_at >= since,
            ~select(literal(1))
            .where(
                recovered_collection.api_name == CollectionTask.api_name,
                recovered_collection.scope_key == CollectionTask.scope_key,
                recovered_collection.status.in_(COLLECTION_SUCCESS),
                recovered_collection.finished_at > CollectionTask.finished_at,
            )
            .exists(),
        )
        processing = (
            select(
                ProcessingTask.process_id.label("id"),
                literal("processing").label("source"),
                ProcessingTask.output_dataset.label("task_name"),
                ProcessingTask.status.label("status"),
                case(
                    (processing_warning, "DATA_QUALITY_WARNING"),
                    else_=None,
                ).label("error_code"),
                case(
                    (processing_warning, ProcessingTask.warning_message),
                    else_=ProcessingTask.error_message,
                ).label("error_message"),
                processing_occurred_at.label("occurred_at"),
            )
            .join(CollectionBatch, CollectionBatch.batch_id == ProcessingTask.source_batch_id)
            .where(
                or_(
                    processing_warning,
                    ProcessingTask.status.in_(
                        (ProcessingTaskStatus.FAILED.value, ProcessingTaskStatus.BLOCKED.value)
                    )
                    & ~select(literal(1))
                    .where(
                        recovered_processing.output_dataset == ProcessingTask.output_dataset,
                        recovered_processing.business_date.is_not_distinct_from(
                            ProcessingTask.business_date
                        ),
                        recovered_processing.status == ProcessingTaskStatus.SUCCESS.value,
                        recovered_processing.finished_at > processing_occurred_at,
                    )
                    .exists(),
                ),
                processing_occurred_at >= since,
            )
        )
        scheduler = select(
            ScheduledJobExecution.execution_id.label("id"),
            literal("scheduler").label("source"),
            ScheduledJobExecution.job_id.label("task_name"),
            ScheduledJobExecution.status.label("status"),
            literal(None).label("error_code"),
            ScheduledJobExecution.error_message.label("error_message"),
            scheduler_occurred_at.label("occurred_at"),
        ).where(
            ScheduledJobExecution.status == "FAILED",
            scheduler_occurred_at >= since,
            ~select(literal(1))
            .where(
                recovered_scheduler.job_id == ScheduledJobExecution.job_id,
                recovered_scheduler.status == "SUCCESS",
                recovered_scheduler.created_at > ScheduledJobExecution.created_at,
            )
            .exists(),
        )
        combined = union_all(collection, processing, scheduler).subquery()
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
            ProcessingTaskStatus.QUEUED.value,
        ),
        "waiting_dependency": (ProcessingTaskStatus.WAITING_DEPENDENCY.value,),
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
        "pending": (ProcessingTaskStatus.QUEUED.value,),
        "waiting_dependency": (ProcessingTaskStatus.WAITING_DEPENDENCY.value,),
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
