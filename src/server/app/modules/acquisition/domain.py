from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from app.modules.acquisition.models import BatchType, CollectionTaskStatus


@dataclass(frozen=True, slots=True)
class TaskBlueprint:
    provider: str
    api_name: str
    scope_key: str
    request_params: dict[str, Any]
    max_attempts: int


@dataclass(frozen=True, slots=True)
class BatchPlanResult:
    batch_id: UUID
    created_task_count: int
    total_task_count: int
    frozen: bool
    plan_version: str | None


@dataclass(frozen=True, slots=True)
class ClaimedCollectionTask:
    task_id: UUID
    batch_id: UUID
    batch_type: BatchType
    business_date: date | None
    provider: str
    api_name: str
    scope_key: str
    request_params: dict[str, Any]
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class RunningTaskSnapshot:
    task_id: UUID
    batch_id: UUID
    business_date: date | None
    provider: str
    api_name: str
    request_params: dict[str, Any]
    attempt_count: int
    max_attempts: int
    started_at: datetime | None


@dataclass(frozen=True, slots=True)
class AssetSnapshot:
    task_id: UUID
    storage_uri: str
    content_hash: str
    schema_fingerprint: str
    row_count: int


@dataclass(frozen=True, slots=True)
class TaskTransition:
    task_id: UUID
    status: CollectionTaskStatus
    next_retry_at: datetime | None


TERMINAL_TASK_STATUSES = frozenset(
    {
        CollectionTaskStatus.SUCCESS.value,
        CollectionTaskStatus.EMPTY_VALID.value,
        CollectionTaskStatus.FAILED.value,
        CollectionTaskStatus.SKIPPED.value,
        CollectionTaskStatus.CANCELLED.value,
    }
)
