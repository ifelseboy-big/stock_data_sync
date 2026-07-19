"""Pydantic contracts for operations dashboards and run diagnostics."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

type ExecutionStatus = Literal[
    "pending",
    "waiting_dependency",
    "running",
    "waiting_retry",
    "succeeded",
    "partial_failed",
    "failed",
    "blocked",
    "closed",
]
type PriorityLevel = Literal[
    "current_normal",
    "auto_supplement",
    "manual_rerun",
    "historical",
]
type AlertLevel = Literal["critical", "warning", "info"]
type ScheduledJobStatus = Literal["pending", "running", "success", "failed"]


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class OperationsModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class PageResult[ItemT](OperationsModel):
    items: list[ItemT]
    total: int
    page: int
    page_size: int
    generated_at: datetime


class OverviewMetrics(OperationsModel):
    collecting_batch_count: int
    processing_task_count: int
    blocked_task_count: int
    task_success_rate_today: float | None
    provider_success_rate_today: float | None
    provider_p95_duration_ms: float | None


class QuotaSnapshot(OperationsModel):
    provider: str
    limit_per_minute: int
    used_in_current_window: int
    remaining_in_current_window: int
    delayed_request_count: int
    captured_at: datetime


class ProcessingQueueItem(OperationsModel):
    id: str
    task_name: str
    batch_code: str
    data_cycle: str
    priority: PriorityLevel
    queue_position: int
    status: ExecutionStatus
    dependency_count: int
    waiting_since: datetime | None
    started_at: datetime | None
    duration_ms: int | None
    blocked_reason: str | None


class AcquisitionBatchItem(OperationsModel):
    id: str
    batch_code: str
    theme_name: str
    data_cycle: str
    batch_type: Literal["normal", "auto_supplement", "manual"]
    status: ExecutionStatus
    task_count: int
    succeeded_task_count: int
    failed_task_count: int
    started_at: datetime | None
    closed_at: datetime | None
    duration_ms: int | None


class DependencyItem(OperationsModel):
    id: str
    processing_task_name: str
    batch_code: str
    source_endpoint: str
    source_scope: str
    source_cycle: str
    source_policy: Literal["current_cycle", "latest_valid"]
    source_ready: bool
    status: ExecutionStatus
    reason: str | None


class ProviderEndpointMetric(OperationsModel):
    endpoint: str
    request_count_today: int
    success_rate_today: float | None
    p50_duration_ms: float | None
    p95_duration_ms: float | None
    throttled_count_today: int
    empty_response_count_today: int
    last_requested_at: datetime | None


class ProviderMonitoring(OperationsModel):
    generated_at: datetime
    quota: QuotaSnapshot | None
    endpoints: list[ProviderEndpointMetric]


class AlertItem(OperationsModel):
    id: str
    level: AlertLevel
    source: str
    title: str
    detail: str
    occurred_at: datetime
    acknowledged_at: datetime | None = None


class RunRecordItem(OperationsModel):
    id: str
    run_type: Literal["acquisition", "processing"]
    task_name: str
    scope_key: str | None
    batch_code: str
    data_cycle: str
    status: ExecutionStatus
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error_summary: str | None


class ScheduledJobItem(OperationsModel):
    job_id: str
    name: str
    category: str
    schedule: str
    enabled: bool
    manual_allowed: bool
    next_run_at: datetime | None
    last_status: ScheduledJobStatus | None
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_duration_ms: int | None
    last_error: str | None


class ScheduledJobExecutionItem(OperationsModel):
    execution_id: str
    job_id: str
    trigger_type: Literal["scheduled", "manual", "startup_catchup"]
    status: ScheduledJobStatus
    requested_by: str | None
    reason: str | None
    scheduled_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error_message: str | None


class DatasetReleaseItem(OperationsModel):
    dataset_name: str
    scope_type: str
    scope_key: str
    business_date: date | None
    version_id: str
    process_id: str
    processor_version: str
    row_count: int
    published_at: datetime


class DatasetReleaseCoverageItem(OperationsModel):
    business_date: date
    expected_count: int
    published_count: int
    missing_datasets: list[str]


class OperationsOverview(OperationsModel):
    generated_at: datetime
    metrics: OverviewMetrics
    quota: QuotaSnapshot | None
    current_processing: ProcessingQueueItem | None
    recent_batches: list[AcquisitionBatchItem]
    recent_alerts: list[AlertItem]


class ManualCommandBase(OperationsModel):
    reason: str = Field(min_length=3, max_length=500)


class CreateBackfillCommand(ManualCommandBase):
    start_date: date
    end_date: date
    api_names: list[str] = Field(min_length=1, max_length=64)


class CreateRepairCommand(ManualCommandBase):
    business_date: date | None = None
    api_names: list[str] = Field(min_length=1, max_length=64)


class TaskCommand(ManualCommandBase):
    pass


class ScheduledJobCommand(ManualCommandBase):
    pass


class OperationCommandResult(OperationsModel):
    command_id: str
    action: str
    target_type: str
    target_id: str | None
    status: Literal["accepted"]
    result: dict[str, Any]
    created_at: datetime
    completed_at: datetime


class AcquisitionApiOption(OperationsModel):
    api_name: str
    schedule_group: str


class ManualCommandOptions(OperationsModel):
    generated_at: datetime
    acquisition_apis: list[AcquisitionApiOption]
    max_backfill_days: int
