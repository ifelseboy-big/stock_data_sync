from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.modules.processing.models import ProcessingTaskStatus


@dataclass(frozen=True, slots=True)
class ClaimedProcessingTask:
    process_id: UUID
    source_batch_id: UUID
    process_type: str
    business_date: date | None
    output_dataset: str
    output_version: UUID
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class RawDependencyAsset:
    dependency_name: str
    scope_key: str
    asset_id: UUID
    storage_uri: str
    content_hash: str
    schema_fingerprint: str
    row_count: int


@dataclass(frozen=True, slots=True)
class ProcessingTransition:
    process_id: UUID
    status: ProcessingTaskStatus
    next_retry_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProcessingPlanResult:
    scanned_batch_count: int
    created_task_count: int
    queued_task_count: int
    blocked_task_count: int
