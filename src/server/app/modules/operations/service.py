from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from app.catalog.datasets import ALL_DATASET_SPECS
from app.catalog.presentation import (
    DATASET_PRESENTATION_BY_NAME,
    TUSHARE_API_PRESENTATION_BY_NAME,
)
from app.catalog.specs import ReleaseScope
from app.catalog.tushare import build_tushare_api_registry
from app.core.config import Settings, settings
from app.modules.acquisition.capacity import CapacityLevel, RawStorageCapacityGate
from app.modules.acquisition.models import BatchStatus, BatchType, CollectionTaskStatus
from app.modules.operations.repository import OperationsRepository
from app.modules.operations.schemas import (
    AcquisitionBatchItem,
    AlertItem,
    DatasetReleaseCoverageItem,
    DatasetReleaseItem,
    DependencyItem,
    DependencySourceSummary,
    ExecutionStatus,
    OperationsOverview,
    OverviewMetrics,
    PageResult,
    PriorityLevel,
    ProcessingQueueItem,
    ProviderEndpointMetric,
    ProviderMonitoring,
    QuotaSnapshot,
    ReadinessStatus,
    RunRecordItem,
    ScheduledJobExecutionItem,
    ScheduledJobItem,
)
from app.modules.processing.models import ProcessingTaskStatus
from app.scheduler.catalog import SCHEDULED_JOB_DEFINITIONS


class OperationsService:
    def __init__(
        self,
        repository: OperationsRepository,
        config: Settings = settings,
    ) -> None:
        self._repository = repository
        self._settings = config
        self._timezone = ZoneInfo(config.scheduler_timezone)

    async def overview(self) -> OperationsOverview:
        now = datetime.now(self._timezone)
        counts = await self._repository.overview_counts(day_start=self._day_start(now))
        quota = await self._quota(now)
        queue = await self.processing_queue(
            status=None,
            dataset_name=None,
            page=1,
            page_size=500,
            now=now,
        )
        batch_rows, _ = await self._repository.acquisition_batches(
            since=now - timedelta(days=30),
            status=None,
            business_date=None,
            offset=0,
            limit=5,
        )
        alerts = await self.alerts(now=now, source=None, page=1, page_size=5)
        task_terminal = cast(int, counts["task_terminal"])
        provider_total = cast(int, counts["provider_total"])
        return OperationsOverview(
            generated_at=now,
            metrics=OverviewMetrics(
                collecting_batch_count=cast(int, counts["collecting_batch_count"]),
                processing_task_count=cast(int, counts["processing_task_count"]),
                blocked_task_count=cast(int, counts["blocked_task_count"]),
                task_success_rate_today=_ratio(cast(int, counts["task_success"]), task_terminal),
                provider_success_rate_today=_ratio(
                    cast(int, counts["provider_success"]), provider_total
                ),
                provider_p95_duration_ms=_optional_float(counts["provider_p95"]),
            ),
            quota=quota,
            current_processing_tasks=[
                item for item in queue.items if item.status == "running"
            ],
            recent_batches=[self._batch_item(row, now) for row in batch_rows],
            recent_alerts=alerts.items,
        )

    async def acquisition_batches(
        self,
        *,
        status: str | None,
        business_date: date | None,
        page: int,
        page_size: int,
    ) -> PageResult[AcquisitionBatchItem]:
        now = datetime.now(self._timezone)
        rows, total = await self._repository.acquisition_batches(
            since=now - timedelta(days=30),
            status=status,
            business_date=business_date,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return PageResult[AcquisitionBatchItem](
            items=[self._batch_item(row, now) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            generated_at=now,
        )

    async def processing_queue(
        self,
        *,
        status: str | None,
        dataset_name: str | None,
        page: int,
        page_size: int,
        now: datetime | None = None,
    ) -> PageResult[ProcessingQueueItem]:
        generated_at = now or datetime.now(self._timezone)
        rows, total = await self._repository.processing_queue(
            status=status,
            dataset_name=dataset_name,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        items: list[ProcessingQueueItem] = []
        queue_position = 0
        for row in rows:
            status = _processing_status(cast(str, row["status"]))
            task_name = cast(str, row["output_dataset"])
            presentation = DATASET_PRESENTATION_BY_NAME.get(task_name)
            if status == "running":
                position = 0
            else:
                queue_position += 1
                position = queue_position
            started_at = cast(datetime | None, row["started_at"])
            finished_at = cast(datetime | None, row["finished_at"])
            items.append(
                ProcessingQueueItem(
                    id=str(row["process_id"]),
                    task_name=task_name,
                    task_display_name=presentation.display_name if presentation else task_name,
                    task_description=(
                        presentation.description
                        if presentation
                        else "执行该数据集的清洗、校验和发布。"
                    ),
                    batch_code=str(row["source_batch_id"]),
                    data_cycle=_cycle(cast(date | None, row["business_date"])),
                    priority=_priority(cast(int, row["priority"])),
                    queue_position=position,
                    status=status,
                    dependency_count=cast(int, row["dependency_count"]),
                    waiting_since=cast(datetime | None, row["queued_at"])
                    or cast(datetime | None, row["next_retry_at"]),
                    started_at=started_at,
                    duration_ms=_duration_ms(started_at, finished_at, generated_at),
                    blocked_reason=cast(str | None, row["error_message"]),
                )
            )
        return PageResult[ProcessingQueueItem](
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            generated_at=generated_at,
        )

    async def dependencies(
        self,
        *,
        readiness: str,
        query: str | None,
        page: int,
        page_size: int,
    ) -> PageResult[DependencyItem]:
        now = datetime.now(self._timezone)
        rows, total = await self._repository.dependencies(
            since=now - timedelta(days=30),
            readiness=readiness,
            query=query,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        source_rows = await self._repository.dependency_source_summaries(
            process_ids=tuple(cast(UUID, row["process_id"]) for row in rows)
        )
        sources_by_process: dict[str, list[DependencySourceSummary]] = {}
        for row in source_rows:
            process_id = str(row["process_id"])
            source_name = cast(str, row["dependency_name"])
            is_raw_asset = cast(str, row["dependency_type"]) == "RAW_ASSET"
            presentation = (
                TUSHARE_API_PRESENTATION_BY_NAME.get(source_name)
                if is_raw_asset
                else DATASET_PRESENTATION_BY_NAME.get(source_name)
            )
            ready_count = cast(int, row["ready_count"])
            waiting_count = cast(int, row["waiting_count"])
            blocked_count = cast(int, row["blocked_count"])
            sources_by_process.setdefault(process_id, []).append(
                DependencySourceSummary(
                    source_type="raw_asset" if is_raw_asset else "dataset_release",
                    source_name=source_name,
                    source_display_name=(
                        presentation.display_name if presentation else source_name
                    ),
                    required_count=cast(int, row["required_count"]),
                    ready_count=ready_count,
                    waiting_count=waiting_count,
                    blocked_count=blocked_count,
                    status=_readiness_status(waiting_count, blocked_count),
                    reason=_localized_error(cast(str | None, row["blocked_reason"])),
                )
            )
        items: list[DependencyItem] = []
        for row in rows:
            process_id = str(row["process_id"])
            task_name = cast(str, row["output_dataset"])
            presentation = DATASET_PRESENTATION_BY_NAME.get(task_name)
            ready_count = cast(int, row["ready_dependency_count"])
            waiting_count = cast(int, row["waiting_dependency_count"])
            blocked_count = cast(int, row["blocked_dependency_count"])
            items.append(
                DependencyItem(
                    id=process_id,
                    processing_task_name=task_name,
                    processing_task_display_name=(
                        presentation.display_name if presentation else task_name
                    ),
                    processing_task_description=(
                        presentation.description
                        if presentation
                        else "完成该数据集的清洗、校验和正式发布。"
                    ),
                    batch_code=str(row["source_batch_id"]),
                    data_cycle=_cycle(cast(date | None, row["business_date"])),
                    processing_status=_processing_status(cast(str, row["processing_status"])),
                    dependency_count=cast(int, row["dependency_count"]),
                    ready_dependency_count=ready_count,
                    waiting_dependency_count=waiting_count,
                    blocked_dependency_count=blocked_count,
                    readiness_status=_readiness_status(waiting_count, blocked_count),
                    reason=_localized_error(cast(str | None, row["error_message"])),
                    sources=sources_by_process.get(process_id, []),
                )
            )
        return PageResult[DependencyItem](
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            generated_at=now,
        )

    async def releases(
        self,
        *,
        dataset_name: str | None,
        page: int,
        page_size: int,
    ) -> PageResult[DatasetReleaseItem]:
        now = datetime.now(self._timezone)
        rows, total = await self._repository.releases(
            dataset_name=dataset_name,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return PageResult[DatasetReleaseItem](
            items=[
                DatasetReleaseItem(
                    dataset_name=cast(str, row["dataset_name"]),
                    scope_type=cast(str, row["scope_type"]),
                    scope_key=cast(str, row["scope_key"]),
                    business_date=cast(date | None, row["business_date"]),
                    version_id=str(row["version_id"]),
                    process_id=str(row["process_id"]),
                    processor_version=cast(str, row["process_type"]).partition("@")[2],
                    row_count=cast(int, row["row_count"]),
                    published_at=cast(datetime, row["published_at"]),
                )
                for row in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
            generated_at=now,
        )

    async def release_coverage(
        self,
        *,
        start_date: date | None,
        end_date: date,
        day_count: int | None,
    ) -> list[DatasetReleaseCoverageItem]:
        expected = tuple(
            sorted(
                spec.dataset_name
                for spec in ALL_DATASET_SPECS
                if spec.release_scope == ReleaseScope.DATE
            )
        )
        rows = await self._repository.release_coverage(
            start_date=start_date,
            end_date=end_date,
            day_count=day_count,
            dataset_names=expected,
        )
        expected_set = set(expected)
        today = datetime.now(self._timezone).date()
        result: list[DatasetReleaseCoverageItem] = []
        for business_date, published in rows:
            missing = sorted(expected_set - published)
            result.append(
                DatasetReleaseCoverageItem(
                    business_date=business_date,
                    expected_count=len(expected),
                    published_count=len(published),
                    coverage_status=(
                        "complete"
                        if not missing
                        else "pending"
                        if business_date == today
                        else "missing"
                    ),
                    missing_datasets=missing,
                    missing_dataset_display_names=[
                        (
                            DATASET_PRESENTATION_BY_NAME[item].display_name
                            if item in DATASET_PRESENTATION_BY_NAME
                            else item
                        )
                        for item in missing
                    ],
                )
            )
        return result

    async def provider_monitoring(self) -> ProviderMonitoring:
        now = datetime.now(self._timezone)
        rows = await self._repository.provider_endpoints(day_start=self._day_start(now))
        observed = {cast(str, row["endpoint"]): row for row in rows}
        return ProviderMonitoring(
            generated_at=now,
            quota=await self._quota(now),
            endpoints=[
                ProviderEndpointMetric(
                    endpoint=spec.api_name,
                    request_count_today=cast(int, row.get("request_count", 0)),
                    success_rate_today=_ratio(
                        cast(int, row.get("success_count", 0)),
                        cast(int, row.get("request_count", 0)),
                    ),
                    p50_duration_ms=_optional_float(row.get("p50")),
                    p95_duration_ms=_optional_float(row.get("p95")),
                    throttled_count_today=cast(int, row.get("throttled_count", 0)),
                    empty_response_count_today=cast(int, row.get("empty_count", 0)),
                    last_requested_at=cast(datetime | None, row.get("last_requested_at")),
                )
                for spec in build_tushare_api_registry().all()
                for row in (observed.get(spec.api_name, {}),)
            ],
        )

    async def run_records(
        self,
        *,
        run_type: str | None,
        status: str | None,
        batch_id: UUID | None,
        unresolved_only: bool,
        page: int,
        page_size: int,
    ) -> PageResult[RunRecordItem]:
        now = datetime.now(self._timezone)
        rows, total = await self._repository.run_records(
            since=now - timedelta(days=30),
            run_type=run_type,
            status=status,
            batch_id=batch_id,
            unresolved_only=unresolved_only,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return PageResult[RunRecordItem](
            items=[self._run_item(row, now) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            generated_at=now,
        )

    async def scheduled_jobs(self) -> list[ScheduledJobItem]:
        controls = await self._repository.scheduled_job_controls()
        latest = await self._repository.latest_scheduled_job_executions()
        next_runs = await self._repository.scheduled_job_next_runs(
            self._settings.scheduler_jobstore_table
        )
        items: list[ScheduledJobItem] = []
        for definition in SCHEDULED_JOB_DEFINITIONS:
            execution = latest.get(definition.job_id, {})
            raw_status = cast(str | None, execution.get("status"))
            items.append(
                ScheduledJobItem(
                    job_id=definition.job_id,
                    name=definition.name,
                    description=definition.description,
                    category=definition.category,
                    schedule=_job_schedule(definition.job_id, definition.schedule, self._settings),
                    enabled=controls.get(definition.job_id, True),
                    manual_allowed=definition.manual_allowed,
                    next_run_at=next_runs.get(definition.job_id),
                    last_status=cast(Any, raw_status.lower() if raw_status else None),
                    last_started_at=cast(datetime | None, execution.get("started_at")),
                    last_finished_at=cast(datetime | None, execution.get("finished_at")),
                    last_duration_ms=cast(int | None, execution.get("duration_ms")),
                    last_error=cast(str | None, execution.get("error_message")),
                )
            )
        return items

    async def scheduled_job_executions(
        self,
        *,
        job_id: str | None,
        status: str | None,
        page: int,
        page_size: int,
    ) -> PageResult[ScheduledJobExecutionItem]:
        now = datetime.now(self._timezone)
        rows, total = await self._repository.scheduled_job_executions(
            job_id=job_id,
            status=status,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return PageResult[ScheduledJobExecutionItem](
            items=[
                ScheduledJobExecutionItem(
                    execution_id=str(row["execution_id"]),
                    job_id=cast(str, row["job_id"]),
                    trigger_type=cast(Any, cast(str, row["trigger_type"]).lower()),
                    status=cast(Any, cast(str, row["status"]).lower()),
                    requested_by=cast(str | None, row["requested_by"]),
                    reason=cast(str | None, row["reason"]),
                    scheduled_at=cast(datetime | None, row["scheduled_at"]),
                    started_at=cast(datetime | None, row["started_at"]),
                    finished_at=cast(datetime | None, row["finished_at"]),
                    duration_ms=cast(int | None, row["duration_ms"]),
                    error_message=cast(str | None, row["error_message"]),
                )
                for row in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
            generated_at=now,
        )

    async def alerts(
        self,
        *,
        now: datetime | None = None,
        source: str | None,
        page: int,
        page_size: int,
    ) -> PageResult[AlertItem]:
        generated_at = now or datetime.now(self._timezone)
        storage_alerts: list[AlertItem] = []
        capacity = RawStorageCapacityGate(
            self._settings.raw_data_dir,
            self._settings,
        ).snapshot()
        if capacity.level != CapacityLevel.NORMAL:
            storage_alerts.append(
                AlertItem(
                    id=f"storage:{capacity.level.value}",
                    level="critical" if capacity.level == CapacityLevel.PROTECT else "warning",
                    source="storage",
                    title="原始数据目录达到容量保护阈值",
                    detail=(f"已用 {capacity.used_percent:.1f}%，剩余 {capacity.free_bytes} 字节"),
                    occurred_at=generated_at,
                ),
            )
        if source and source != "storage":
            storage_alerts = []
        first_offset = (page - 1) * page_size
        storage_count = len(storage_alerts)
        database_offset = max(0, first_offset - storage_count)
        database_limit = max(0, page_size - max(0, storage_count - first_offset))
        rows, database_total = await self._repository.alert_rows(
            since=generated_at - timedelta(days=30),
            source=source if source != "storage" else "__NO_MATCH__",
            offset=database_offset,
            limit=database_limit,
        )
        page_storage = storage_alerts[first_offset : first_offset + page_size]
        alerts = page_storage + [self._alert_item(row, generated_at) for row in rows]
        return PageResult[AlertItem](
            items=alerts,
            total=database_total + storage_count,
            page=page,
            page_size=page_size,
            generated_at=generated_at,
        )

    async def _quota(self, now: datetime) -> QuotaSnapshot:
        counts = await self._repository.quota_counts(
            window_start=now.astimezone(UTC) - timedelta(seconds=60)
        )
        limit = self._settings.tushare_request_budget_per_minute
        return QuotaSnapshot(
            provider="tushare",
            limit_per_minute=limit,
            used_in_current_window=counts["used"],
            remaining_in_current_window=max(0, limit - counts["used"]),
            delayed_request_count=counts["delayed"],
            captured_at=now,
        )

    def _batch_item(
        self,
        row: dict[str, Any],
        now: datetime,
    ) -> AcquisitionBatchItem:
        batch_type = cast(str, row["batch_type"])
        started_at = cast(datetime | None, row["started_at"])
        closed_at = cast(datetime | None, row["closed_at"])
        failed_count = cast(int, row["failed_count"])
        return AcquisitionBatchItem(
            id=str(row["batch_id"]),
            batch_code=str(row["batch_id"]),
            theme_name=_batch_theme(batch_type),
            data_cycle=_cycle(cast(date | None, row["business_date"])),
            batch_type=_batch_type(batch_type),
            status=_batch_status(cast(str, row["batch_status"]), failed_count),
            task_count=cast(int, row["task_count"]),
            succeeded_task_count=cast(int, row["success_count"]),
            failed_task_count=failed_count,
            started_at=started_at,
            closed_at=closed_at,
            duration_ms=_duration_ms(started_at, closed_at, now),
        )

    def _run_item(self, row: dict[str, Any], now: datetime) -> RunRecordItem:
        run_type = cast(str, row["run_type"])
        task_name = cast(str, row["task_name"])
        presentation = (
            TUSHARE_API_PRESENTATION_BY_NAME.get(task_name)
            if run_type == "acquisition"
            else DATASET_PRESENTATION_BY_NAME.get(task_name)
        )
        started_at = cast(datetime | None, row["started_at"])
        finished_at = cast(datetime | None, row["finished_at"])
        return RunRecordItem(
            id=str(row["id"]),
            run_type=cast(Any, run_type),
            task_name=task_name,
            task_display_name=presentation.display_name if presentation else task_name,
            task_description=(
                presentation.description if presentation else "执行该任务的数据处理流程。"
            ),
            scope_key=cast(str | None, row["scope_key"]),
            batch_code=str(row["batch_id"]),
            data_cycle=_cycle(cast(date | None, row["business_date"])),
            status=(
                _collection_status(cast(str, row["raw_status"]))
                if run_type == "acquisition"
                else _processing_status(cast(str, row["raw_status"]))
            ),
            attempt=cast(int, row["attempt"]),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=_duration_ms(started_at, finished_at, now),
            error_summary=_localized_error(cast(str | None, row["error_message"])),
        )

    @staticmethod
    def _alert_item(row: dict[str, Any], now: datetime) -> AlertItem:
        source = cast(str, row["source"])
        task_name = cast(str, row["task_name"])
        error_code = cast(str | None, row["error_code"])
        critical_codes = {"TOKEN_INVALID", "PERMISSION_DENIED", "SCHEMA_CHANGED"}
        is_data_quality_warning = error_code in {
            "DATA_QUALITY_WARNING",
            "DATA_GAP_WARNING",
        }
        level = (
            "critical"
            if error_code in critical_codes or cast(str, row["status"]) == "FAILED"
            else "warning"
        )
        return AlertItem(
            id=f"{source}:{row['id']}",
            level=cast(Any, level),
            source=source,
            title=(
                f"{task_name} 数据缺口提醒"
                if error_code == "DATA_GAP_WARNING"
                else f"{task_name} 数据质量提醒"
                if is_data_quality_warning
                else f"{task_name} 执行异常"
            ),
            detail=_localized_error(cast(str | None, row["error_message"]))
            or error_code
            or "任务进入异常状态",
            occurred_at=cast(datetime | None, row["occurred_at"]) or now,
        )

    def _day_start(self, now: datetime) -> datetime:
        return datetime.combine(now.date(), time.min, self._timezone).astimezone(UTC)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _optional_float(value: object) -> float | None:
    return float(cast(Any, value)) if value is not None else None


def _job_schedule(job_id: str, schedule: str, config: Settings) -> str:
    if job_id in {"close-collection-batches", "plan-processing-tasks"}:
        return f"每 {config.scheduler_poll_seconds} 秒"
    return schedule


def _localized_error(message: str | None) -> str | None:
    if message is None:
        return None
    translations = {
        "one or more required dependencies are unavailable": "一个或多个必要依赖不可用",
        "scheduler stopped while processing task was running": "调度器停止时加工任务仍在运行",
        "scheduler stopped while the collection task was running": "调度器停止时采集任务仍在运行",
        "no complete raw asset was available when the batch closed": (
            "批次关闭时没有可用的完整原始数据"
        ),
        "provider returned no rows for a required scope": "接口在该采集范围内未返回必要数据",
        "release unavailable": "所依赖的数据集尚未发布",
        "required asset is missing": "必要的原始数据缺失",
    }
    return translations.get(message, message)


def _duration_ms(
    started_at: datetime | None,
    finished_at: datetime | None,
    now: datetime,
) -> int | None:
    if started_at is None:
        return None
    end = finished_at or now
    return max(0, round((end - started_at).total_seconds() * 1000))


def _cycle(value: date | None) -> str:
    return value.isoformat() if value else "GLOBAL"


def _batch_type(value: str) -> str:
    if value == BatchType.REPAIR.value:
        return "manual"
    if value == BatchType.BACKFILL.value:
        return "manual"
    return "normal"


def _batch_theme(value: str) -> str:
    return {
        BatchType.MASTER.value: "主数据",
        BatchType.DAILY.value: "日频数据",
        BatchType.HOT.value: "热榜数据",
        BatchType.DELAYED.value: "延迟数据",
        BatchType.BACKFILL.value: "历史回填",
        BatchType.REPAIR.value: "数据修复",
    }.get(value, value)


def _batch_status(value: str, failed_count: int) -> ExecutionStatus:
    if value == BatchStatus.PENDING.value:
        return "pending"
    if value == BatchStatus.RUNNING.value:
        return "running"
    if value == BatchStatus.CLOSED.value:
        return "partial_failed" if failed_count else "succeeded"
    return "failed"


def _collection_status(value: str) -> ExecutionStatus:
    return cast(
        ExecutionStatus,
        {
            CollectionTaskStatus.PENDING.value: "pending",
            CollectionTaskStatus.RUNNING.value: "running",
            CollectionTaskStatus.RETRY_WAIT.value: "waiting_retry",
            CollectionTaskStatus.SUCCESS.value: "succeeded",
            CollectionTaskStatus.EMPTY_VALID.value: "succeeded",
            CollectionTaskStatus.FAILED.value: "failed",
            CollectionTaskStatus.SKIPPED.value: "failed",
            CollectionTaskStatus.CANCELLED.value: "failed",
        }[value],
    )


def _processing_status(value: str) -> ExecutionStatus:
    return cast(
        ExecutionStatus,
        {
            ProcessingTaskStatus.WAITING_DEPENDENCY.value: "waiting_dependency",
            ProcessingTaskStatus.QUEUED.value: "pending",
            ProcessingTaskStatus.RUNNING.value: "running",
            ProcessingTaskStatus.RETRY_WAIT.value: "waiting_retry",
            ProcessingTaskStatus.SUCCESS.value: "succeeded",
            ProcessingTaskStatus.BLOCKED.value: "blocked",
            ProcessingTaskStatus.FAILED.value: "failed",
            ProcessingTaskStatus.SKIPPED.value: "failed",
            ProcessingTaskStatus.CANCELLED.value: "failed",
        }[value],
    )


def _readiness_status(waiting_count: int, blocked_count: int) -> ReadinessStatus:
    if blocked_count > 0:
        return "blocked"
    if waiting_count > 0:
        return "waiting"
    return "ready"


def _priority(value: int) -> PriorityLevel:
    if value <= 100:
        return "current_normal"
    if value <= 200:
        return "auto_supplement"
    if value <= 300:
        return "manual_rerun"
    return "historical"
