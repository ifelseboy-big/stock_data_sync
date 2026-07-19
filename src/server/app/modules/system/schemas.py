from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    status: str
    database: str | None = None


class AdminConfigResponse(BaseModel):
    admin_api_token: str


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ResourceModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class DatabaseResources(ResourceModel):
    status: str
    version: str
    size_bytes: int
    shared_buffers_bytes: int
    active_connection_count: int
    long_transaction_count: int


class SchedulerResources(ResourceModel):
    status: str
    singleton_lock_held: bool
    processing_running_count: int
    processing_max_workers: int


class StorageResources(ResourceModel):
    level: str
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_percent: float


class ProcessResources(ResourceModel):
    app_version: str
    cpu_count: int
    load_average_one_minute: float | None
    memory_high_water_bytes: int


class SystemResources(ResourceModel):
    generated_at: datetime
    database: DatabaseResources
    scheduler: SchedulerResources
    storage: StorageResources
    process: ProcessResources
