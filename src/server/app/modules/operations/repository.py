from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    MetaData,
    String,
    Table,
    and_,
    case,
    func,
    inspect,
    literal,
    or_,
    select,
    union_all,
)
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
COLLECTION_ACTIVE = (
    CollectionTaskStatus.PENDING.value,
    CollectionTaskStatus.RUNNING.value,
    CollectionTaskStatus.RETRY_WAIT.value,
)
PROCESSING_SUCCESS = (ProcessingTaskStatus.SUCCESS.value,)
PROCESSING_FAILED = (
    ProcessingTaskStatus.FAILED.value,
    ProcessingTaskStatus.SKIPPED.value,
    ProcessingTaskStatus.CANCELLED.value,
)
PROCESSING_TERMINAL = (*PROCESSING_SUCCESS, *PROCESSING_FAILED)
PROCESSING_ACTIVE = (
    ProcessingTaskStatus.WAITING_DEPENDENCY.value,
    ProcessingTaskStatus.QUEUED.value,
    ProcessingTaskStatus.RUNNING.value,
    ProcessingTaskStatus.RETRY_WAIT.value,
    ProcessingTaskStatus.BLOCKED.value,
)
DATASETS_BY_RELEASE_SCOPE = {
    scope: tuple(
        spec.dataset_name for spec in ALL_DATASET_SPECS if spec.release_scope == scope
    )
    for scope in ReleaseScope
}
DATE_SCOPED_DATASETS = DATASETS_BY_RELEASE_SCOPE[ReleaseScope.DATE]
MONTH_SCOPED_DATASETS = DATASETS_BY_RELEASE_SCOPE[ReleaseScope.MONTH]
ENTITY_SCOPED_DATASETS = DATASETS_BY_RELEASE_SCOPE[ReleaseScope.ENTITY]


class OperationsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _windowed_page(
        self,
        statement: Any,
        *,
        name: str,
        order_by: Callable[[Any], tuple[Any, ...]],
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        page_source = statement.subquery(name)
        total_column = "_page_total_count"
        page_statement = (
            select(
                page_source,
                func.count().over().label(total_column),
            )
            .order_by(*order_by(page_source))
            .offset(offset)
            .limit(limit)
        )
        rows = [
            dict(row)
            for row in (await self._session.execute(page_statement)).mappings()
        ]
        if rows:
            total = int(rows[0][total_column])
            for row in rows:
                row.pop(total_column, None)
            return rows, total
        if not offset:
            return [], 0
        fallback_total = await self._session.scalar(
            select(func.count()).select_from(page_source)
        )
        return [], int(fallback_total or 0)

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
        recovered_blocked = aliased(DatasetRelease)
        blocked = await self._session.scalar(
            select(func.count())
            .select_from(ProcessingTask)
            .join(CollectionBatch, CollectionBatch.batch_id == ProcessingTask.source_batch_id)
            .where(
                ProcessingTask.status == ProcessingTaskStatus.BLOCKED.value,
                ~select(literal(1))
                .where(
                    _release_matches_processing_scope(
                        recovered_blocked,
                        task_name=ProcessingTask.output_dataset,
                        business_date=ProcessingTask.business_date,
                    ),
                    recovered_blocked.published_at > CollectionBatch.scheduled_at,
                )
                .exists(),
            )
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
        return await self._windowed_page(
            filtered,
            name="filtered_acquisition_batches",
            order_by=lambda rows: (rows.c.scheduled_at.desc(), rows.c.batch_id),
            offset=offset,
            limit=limit,
        )

    async def processing_queue(
        self,
        *,
        status: str | None,
        dataset_name: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        recovered_release = aliased(DatasetRelease)
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
            .where(
                ~select(literal(1))
                .where(
                    _release_matches_processing_scope(
                        recovered_release,
                        task_name=ProcessingTask.output_dataset,
                        business_date=ProcessingTask.business_date,
                    ),
                    recovered_release.published_at
                    > func.coalesce(
                        ProcessingTask.finished_at,
                        ProcessingTask.started_at,
                        ProcessingTask.queued_at,
                        CollectionBatch.scheduled_at,
                    ),
                )
                .exists()
            )
            .group_by(ProcessingTask.process_id, CollectionBatch.batch_type)
        )
        if dataset_name:
            statement = statement.where(ProcessingTask.output_dataset == dataset_name)
        status_values = _processing_status_values(status)
        if status_values is not None:
            statement = statement.where(ProcessingTask.status.in_(status_values))
        return await self._windowed_page(
            statement,
            name="filtered_processing_queue",
            order_by=lambda rows: (
                case(
                    (rows.c.status == ProcessingTaskStatus.RUNNING.value, 0),
                    else_=1,
                ),
                rows.c.priority,
                rows.c.business_date.asc().nullsfirst(),
                rows.c.queued_at.asc().nullsfirst(),
                rows.c.process_id,
            ),
            offset=offset,
            limit=limit,
        )

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
                    _release_matches_processing_scope(
                        recovered_release,
                        task_name=ProcessingTask.output_dataset,
                        business_date=ProcessingTask.business_date,
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
        if readiness in {"attention", "waiting", "blocked"}:
            statement = statement.where(
                ProcessingTask.status.not_in(
                    (
                        ProcessingTaskStatus.SUCCESS.value,
                        ProcessingTaskStatus.SKIPPED.value,
                        ProcessingTaskStatus.CANCELLED.value,
                    )
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
        return await self._windowed_page(
            filtered,
            name="filtered_dependencies",
            order_by=lambda rows: (
                case((rows.c.blocked_dependency_count > 0, 0), else_=1),
                case((rows.c.waiting_dependency_count > 0, 0), else_=1),
                rows.c.scheduled_at.desc(),
                rows.c.output_dataset,
                rows.c.process_id,
            ),
            offset=offset,
            limit=limit,
        )

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
        return await self._windowed_page(
            statement,
            name="filtered_releases",
            order_by=lambda rows: (
                rows.c.published_at.desc(),
                rows.c.dataset_name,
                rows.c.scope_key,
            ),
            offset=offset,
            limit=limit,
        )

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
            func.coalesce(
                CollectionTask.warning_message,
                CollectionTask.error_message,
            ).label("error_message"),
            CollectionBatch.scheduled_at.label("sort_time"),
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
        ).join(CollectionBatch, CollectionBatch.batch_id == ProcessingTask.source_batch_id)
        statements = []
        if run_type in (None, "acquisition"):
            collection_statement = collection.where(CollectionBatch.scheduled_at >= since)
            collection_statuses = _collection_run_status_values(status)
            if collection_statuses is not None:
                collection_statement = collection_statement.where(
                    CollectionTask.status.in_(collection_statuses)
                )
            statements.append(collection_statement)
        if run_type in (None, "processing"):
            processing_statement = processing.where(CollectionBatch.scheduled_at >= since)
            processing_statuses = _processing_run_status_values(status)
            if processing_statuses is not None:
                processing_statement = processing_statement.where(
                    ProcessingTask.status.in_(processing_statuses)
                )
            statements.append(processing_statement)
        combined = union_all(*statements).subquery("combined_runs")
        filtered = select(combined)
        if batch_id is not None:
            filtered = filtered.where(combined.c.batch_id == batch_id)

        if unresolved_only:
            candidates = filtered.subquery("candidate_runs")
            unresolved = (
                select(candidates)
                .where(
                    _run_recovered_expression(candidates, run_type=run_type).is_(False),
                    or_(candidates.c.run_type == "acquisition", candidates.c.attempt > 0),
                )
                .subquery("unresolved_runs")
            )
            logical_scope = case(
                (
                    unresolved.c.run_type == "acquisition",
                    func.coalesce(unresolved.c.scope_key, literal("global")),
                ),
                else_=func.coalesce(
                    sql_cast(unresolved.c.business_date, String),
                    literal("global"),
                ),
            )
            ranked_unresolved = select(
                unresolved,
                func.row_number()
                .over(
                    partition_by=(
                        unresolved.c.run_type,
                        unresolved.c.task_name,
                        logical_scope,
                    ),
                    order_by=(
                        unresolved.c.sort_time.desc(),
                        unresolved.c.finished_at.desc().nullslast(),
                        unresolved.c.id,
                    ),
                )
                .label("logical_rank"),
            ).subquery("ranked_unresolved_runs")
            filtered = select(ranked_unresolved).where(ranked_unresolved.c.logical_rank == 1)

        return await self._windowed_page(
            filtered,
            name="filtered_runs",
            order_by=lambda rows: (rows.c.sort_time.desc(), rows.c.id),
            offset=offset,
            limit=limit,
        )

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
        return await self._windowed_page(
            statement,
            name="filtered_scheduled_job_executions",
            order_by=lambda rows: (rows.c.created_at.desc(), rows.c.execution_id),
            offset=offset,
            limit=limit,
        )

    async def alert_rows(
        self,
        *,
        since: datetime,
        category: str,
        source: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        failed_collection_batch = aliased(CollectionBatch)
        recovered_processing_release = aliased(DatasetRelease)
        recovering_processing = aliased(ProcessingTask)
        recovering_processing_batch = aliased(CollectionBatch)
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
            & ~or_(
                ProcessingTask.warning_message.like("%个证券历史代码映射为现行代码%"),
                ProcessingTask.warning_message.like(
                    "%重复记录仅有名称或数值精度差异，已确定性合并%"
                ),
                ProcessingTask.warning_message.like("%完全重复记录，加工时已确定性去重%"),
            )
        )
        collection_warning = (
            CollectionTask.status == CollectionTaskStatus.EMPTY_VALID.value
        ) & CollectionTask.warning_message.is_not(None)
        scheduler_occurred_at = func.coalesce(
            ScheduledJobExecution.finished_at,
            ScheduledJobExecution.started_at,
            ScheduledJobExecution.created_at,
        )
        collection_recovered = _collection_recovered_expression(
            task_name=CollectionTask.api_name,
            scope_key=CollectionTask.scope_key,
            finished_at=CollectionTask.finished_at,
            sort_time=failed_collection_batch.scheduled_at,
        )
        processing_recovered = or_(
            select(literal(1))
            .where(
                _release_matches_processing_scope(
                    recovered_processing_release,
                    task_name=ProcessingTask.output_dataset,
                    business_date=ProcessingTask.business_date,
                ),
                recovered_processing_release.published_at > processing_occurred_at,
            )
            .exists(),
            select(literal(1))
            .select_from(recovering_processing)
            .join(
                recovering_processing_batch,
                recovering_processing_batch.batch_id == recovering_processing.source_batch_id,
            )
            .where(
                _processing_tasks_share_scope(
                    recovering_processing,
                    task_name=ProcessingTask.output_dataset,
                    business_date=ProcessingTask.business_date,
                ),
                recovering_processing.status.in_(PROCESSING_ACTIVE),
                func.coalesce(
                    recovering_processing.queued_at,
                    recovering_processing.started_at,
                    recovering_processing_batch.scheduled_at,
                )
                > processing_occurred_at,
            )
            .exists(),
        )
        collection_failure = (
            select(
                CollectionTask.task_id.label("id"),
                literal("acquisition").label("source"),
                literal("action_required").label("category"),
                CollectionTask.api_name.label("task_name"),
                CollectionTask.status.label("status"),
                CollectionTask.error_code.label("error_code"),
                CollectionTask.error_message.label("error_message"),
                CollectionTask.finished_at.label("occurred_at"),
                (
                    literal("failure:")
                    + CollectionTask.provider
                    + literal(":")
                    + CollectionTask.api_name
                    + literal(":")
                    + CollectionTask.scope_key
                ).label("group_key"),
            )
            .join(
                failed_collection_batch,
                failed_collection_batch.batch_id == CollectionTask.batch_id,
            )
            .where(
                CollectionTask.finished_at >= since,
                CollectionTask.status == CollectionTaskStatus.FAILED.value,
                ~collection_recovered,
            )
        )
        collection_gap = (
            select(
                CollectionTask.task_id.label("id"),
                literal("acquisition").label("source"),
                literal("data_gap").label("category"),
                CollectionTask.api_name.label("task_name"),
                CollectionTask.status.label("status"),
                literal("DATA_GAP_WARNING").label("error_code"),
                CollectionTask.warning_message.label("error_message"),
                CollectionTask.finished_at.label("occurred_at"),
                (literal("warning:") + CollectionTask.api_name).label("group_key"),
            )
            .join(
                failed_collection_batch,
                failed_collection_batch.batch_id == CollectionTask.batch_id,
            )
            .where(
                CollectionTask.finished_at >= since,
                collection_warning,
            )
        )
        processing_failure = (
            select(
                ProcessingTask.process_id.label("id"),
                literal("processing").label("source"),
                literal("action_required").label("category"),
                ProcessingTask.output_dataset.label("task_name"),
                ProcessingTask.status.label("status"),
                literal(None).cast(String).label("error_code"),
                ProcessingTask.error_message.label("error_message"),
                processing_occurred_at.label("occurred_at"),
                (
                    literal("failure:")
                    + ProcessingTask.output_dataset
                    + literal(":")
                    + func.coalesce(
                        sql_cast(ProcessingTask.business_date, String),
                        literal("global"),
                    )
                ).label("group_key"),
            )
            .join(CollectionBatch, CollectionBatch.batch_id == ProcessingTask.source_batch_id)
            .where(
                ProcessingTask.status == ProcessingTaskStatus.FAILED.value,
                ~processing_recovered,
                processing_occurred_at >= since,
            )
        )
        processing_quality = (
            select(
                ProcessingTask.process_id.label("id"),
                literal("processing").label("source"),
                literal("quality").label("category"),
                ProcessingTask.output_dataset.label("task_name"),
                ProcessingTask.status.label("status"),
                literal("DATA_QUALITY_WARNING").label("error_code"),
                ProcessingTask.warning_message.label("error_message"),
                processing_occurred_at.label("occurred_at"),
                (literal("warning:") + ProcessingTask.output_dataset).label("group_key"),
            )
            .join(CollectionBatch, CollectionBatch.batch_id == ProcessingTask.source_batch_id)
            .where(
                processing_warning,
                processing_occurred_at >= since,
            )
        )
        scheduler_failure = select(
            ScheduledJobExecution.execution_id.label("id"),
            literal("scheduler").label("source"),
            literal("action_required").label("category"),
            ScheduledJobExecution.job_id.label("task_name"),
            ScheduledJobExecution.status.label("status"),
            literal(None).cast(String).label("error_code"),
            ScheduledJobExecution.error_message.label("error_message"),
            scheduler_occurred_at.label("occurred_at"),
            (literal("scheduler:") + ScheduledJobExecution.job_id).label("group_key"),
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

        branches: list[Any] = []
        if source in {None, "acquisition"}:
            if category in {"action_required", "all"}:
                branches.append(collection_failure)
            if category in {"data_gap", "all"}:
                branches.append(collection_gap)
        if source in {None, "processing"}:
            if category in {"action_required", "all"}:
                branches.append(processing_failure)
            if category in {"quality", "all"}:
                branches.append(processing_quality)
        if source in {None, "scheduler"} and category in {"action_required", "all"}:
            branches.append(scheduler_failure)
        if not branches:
            return [], 0

        combined_query = branches[0] if len(branches) == 1 else union_all(*branches)
        combined = combined_query.subquery("alert_candidates")
        ranked = select(
            combined,
            func.count()
            .over(
                partition_by=(
                    combined.c.category,
                    combined.c.source,
                    combined.c.group_key,
                )
            )
            .label("alert_count"),
            func.row_number()
            .over(
                partition_by=(
                    combined.c.category,
                    combined.c.source,
                    combined.c.group_key,
                ),
                order_by=(combined.c.occurred_at.desc().nullslast(), combined.c.id),
            )
            .label("alert_rank"),
        ).subquery()
        statement = select(
            ranked.c.id,
            ranked.c.source,
            ranked.c.category,
            ranked.c.task_name,
            ranked.c.status,
            ranked.c.error_code,
            case(
                (
                    ranked.c.alert_count > 1,
                    func.concat(
                        "同类告警共 ",
                        ranked.c.alert_count,
                        " 条，已聚合显示；最新记录：",
                        ranked.c.error_message,
                    ),
                ),
                else_=ranked.c.error_message,
            ).label("error_message"),
            ranked.c.occurred_at,
        ).where(ranked.c.alert_rank == 1)
        return await self._windowed_page(
            statement,
            name="filtered_alerts",
            order_by=lambda rows: (rows.c.occurred_at.desc().nullslast(), rows.c.id),
            offset=offset,
            limit=limit,
        )


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


def _collection_run_status_values(status: str | None) -> tuple[str, ...] | None:
    if status is None:
        return None
    return {
        "pending": (CollectionTaskStatus.PENDING.value,),
        "running": (CollectionTaskStatus.RUNNING.value,),
        "waiting_retry": (CollectionTaskStatus.RETRY_WAIT.value,),
        "succeeded": COLLECTION_SUCCESS,
        "failed": COLLECTION_FAILED,
    }.get(status, ("__NO_MATCH__",))


def _processing_run_status_values(status: str | None) -> tuple[str, ...] | None:
    if status is None:
        return None
    return {
        "pending": (ProcessingTaskStatus.QUEUED.value,),
        "waiting_dependency": (ProcessingTaskStatus.WAITING_DEPENDENCY.value,),
        "running": (ProcessingTaskStatus.RUNNING.value,),
        "waiting_retry": (ProcessingTaskStatus.RETRY_WAIT.value,),
        "succeeded": PROCESSING_SUCCESS,
        "failed": PROCESSING_FAILED,
        "blocked": (ProcessingTaskStatus.BLOCKED.value,),
    }.get(status, ("__NO_MATCH__",))


def _collection_recovered_expression(
    *,
    task_name: Any,
    scope_key: Any,
    finished_at: Any,
    sort_time: Any,
) -> Any:
    recovered = aliased(CollectionTask)
    recovering = aliased(CollectionTask)
    recovering_batch = aliased(CollectionBatch)

    same_scope_recovered = (
        select(literal(1))
        .select_from(recovered)
        .where(
            recovered.api_name == task_name,
            recovered.scope_key == scope_key,
            recovered.status.in_(COLLECTION_SUCCESS),
            recovered.finished_at > finished_at,
        )
        .exists()
    )
    same_scope_recovering = (
        select(literal(1))
        .select_from(recovering)
        .join(recovering_batch, recovering_batch.batch_id == recovering.batch_id)
        .where(
            recovering.api_name == task_name,
            recovering.scope_key == scope_key,
            recovering.status.in_(COLLECTION_ACTIVE),
            recovering_batch.scheduled_at > sort_time,
        )
        .exists()
    )
    return or_(same_scope_recovered, same_scope_recovering)


def _run_recovered_expression(rows: Any, *, run_type: str | None) -> Any:
    recovered_release = aliased(DatasetRelease)
    recovering_processing = aliased(ProcessingTask)
    recovering_processing_batch = aliased(CollectionBatch)
    collection_recovered = _collection_recovered_expression(
        task_name=rows.c.task_name,
        scope_key=rows.c.scope_key,
        finished_at=rows.c.finished_at,
        sort_time=rows.c.sort_time,
    )
    processing_recovered = or_(
        select(literal(1))
        .where(
            _release_matches_processing_scope(
                recovered_release,
                task_name=rows.c.task_name,
                business_date=rows.c.business_date,
            ),
            recovered_release.published_at > rows.c.sort_time,
        )
        .exists(),
        select(literal(1))
        .select_from(recovering_processing)
        .join(
            recovering_processing_batch,
            recovering_processing_batch.batch_id == recovering_processing.source_batch_id,
        )
        .where(
            _processing_tasks_share_scope(
                recovering_processing,
                task_name=rows.c.task_name,
                business_date=rows.c.business_date,
            ),
            recovering_processing.status.in_(PROCESSING_ACTIVE),
            func.coalesce(
                recovering_processing.queued_at,
                recovering_processing.started_at,
                recovering_processing_batch.scheduled_at,
            )
            > func.coalesce(rows.c.finished_at, rows.c.sort_time),
        )
        .exists(),
    )
    if run_type == "acquisition":
        return collection_recovered.label("recovered")
    if run_type == "processing":
        return processing_recovered.label("recovered")
    return case(
        (rows.c.run_type == "acquisition", collection_recovered),
        else_=processing_recovered,
    ).label("recovered")


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


def _processing_scope_type_expression(task_name: Any) -> Any:
    branches = tuple(
        (task_name.in_(dataset_names), literal(scope.value))
        for scope, dataset_names in DATASETS_BY_RELEASE_SCOPE.items()
        if scope != ReleaseScope.GLOBAL and dataset_names
    )
    return case(*branches, else_=literal(ReleaseScope.GLOBAL.value))


def _processing_scope_key_expression(task_name: Any, business_date: Any) -> Any:
    branches: list[tuple[Any, Any]] = []
    if DATE_SCOPED_DATASETS:
        branches.append(
            (task_name.in_(DATE_SCOPED_DATASETS), sql_cast(business_date, String))
        )
    if MONTH_SCOPED_DATASETS:
        branches.append(
            (task_name.in_(MONTH_SCOPED_DATASETS), func.to_char(business_date, "YYYY-MM"))
        )
    if ENTITY_SCOPED_DATASETS:
        branches.append(
            (task_name.in_(ENTITY_SCOPED_DATASETS), sql_cast(business_date, String))
        )
    return case(*branches, else_=literal("GLOBAL"))


def _release_matches_processing_scope(
    release: Any,
    *,
    task_name: Any,
    business_date: Any,
) -> Any:
    return and_(
        release.dataset_name == task_name,
        release.scope_type == _processing_scope_type_expression(task_name),
        release.scope_key == _processing_scope_key_expression(task_name, business_date),
    )


def _processing_tasks_share_scope(
    candidate: Any,
    *,
    task_name: Any,
    business_date: Any,
) -> Any:
    return and_(
        candidate.output_dataset == task_name,
        _processing_scope_key_expression(
            candidate.output_dataset,
            candidate.business_date,
        )
        == _processing_scope_key_expression(task_name, business_date),
    )


def _dependency_status_values(status: str | None) -> tuple[str, ...] | None:
    if status is None:
        return None
    return {
        "pending": (DependencyStatus.WAITING.value,),
        "succeeded": (DependencyStatus.READY.value,),
        "blocked": (DependencyStatus.MISSING.value, DependencyStatus.FAILED.value),
    }.get(status, ("__NO_MATCH__",))
