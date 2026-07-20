from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, time
from enum import StrEnum
from types import MappingProxyType

import pyarrow as pa

type ParameterValue = str | int | float | bool | date | tuple[str, ...] | None
type ScopeBuilder = Callable[[date | None], Iterable["RequestScope"]]
type DateExtractor = Callable[[Mapping[str, object]], date | None]
type ExpectedRowCount = Callable[[Mapping[str, object]], int | None]


class ScheduleGroup(StrEnum):
    MASTER = "MASTER"
    DAILY = "DAILY"
    HOT = "HOT"
    DELAYED = "DELAYED"
    BACKFILL = "BACKFILL"


class SplitPolicy(StrEnum):
    NONE = "NONE"
    OFFSET = "OFFSET"
    TRADE_DATE = "TRADE_DATE"
    SECURITY = "SECURITY"
    BOARD = "BOARD"
    THEME = "THEME"
    INDEX = "INDEX"
    MONTH = "MONTH"


class EmptyPolicy(StrEnum):
    ALLOWED = "ALLOWED"
    RETRY_UNTIL_CUTOFF = "RETRY_UNTIL_CUTOFF"
    FORBIDDEN = "FORBIDDEN"
    UNSUPPORTED = "UNSUPPORTED"


class WriteStrategy(StrEnum):
    UPSERT_KEY = "UPSERT_KEY"
    REPLACE_DATE = "REPLACE_DATE"
    REPLACE_ENTITY = "REPLACE_ENTITY"
    MASTER_MERGE = "MASTER_MERGE"
    PATCH_COLUMNS = "PATCH_COLUMNS"


class ReleaseScope(StrEnum):
    GLOBAL = "GLOBAL"
    DATE = "DATE"
    MONTH = "MONTH"
    ENTITY = "ENTITY"


class DependencyKind(StrEnum):
    RAW_ASSET = "RAW_ASSET"
    DATASET_RELEASE = "DATASET_RELEASE"


@dataclass(frozen=True, slots=True)
class RequestScope:
    scope_key: str
    params: Mapping[str, ParameterValue]

    def __post_init__(self) -> None:
        if not self.scope_key or len(self.scope_key) > 256:
            raise ValueError("scope_key must contain 1 to 256 characters")
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_wait_seconds: float = 2
    max_wait_seconds: float = 300
    cutoff_time: time | None = None
    retryable_error_codes: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.initial_wait_seconds <= 0:
            raise ValueError("initial_wait_seconds must be positive")
        if self.max_wait_seconds < self.initial_wait_seconds:
            raise ValueError("max_wait_seconds cannot be less than initial_wait_seconds")


@dataclass(frozen=True, slots=True)
class ApiSpec:
    api_name: str
    provider: str
    fields: tuple[str, ...]
    schema: pa.Schema
    natural_key: tuple[str, ...]
    schedule_group: ScheduleGroup
    scope_builder: ScopeBuilder
    split_policy: SplitPolicy
    row_limit: int | None
    empty_policy: EmptyPolicy
    retry_policy: RetryPolicy
    date_extractor: DateExtractor
    historical_scope_builder: ScopeBuilder | None = None
    endpoint_budget_per_minute: int | None = None
    daily_quota: int | None = None
    expected_row_count: ExpectedRowCount | None = None
    historical_retention_months: int | None = None

    def __post_init__(self) -> None:
        if not self.api_name or not self.provider:
            raise ValueError("api_name and provider are required")
        if not self.fields or len(set(self.fields)) != len(self.fields):
            raise ValueError("fields must be non-empty and unique while preserving order")
        if tuple(self.schema.names) != self.fields:
            raise ValueError("Arrow schema field order must match fields")
        if not set(self.natural_key) <= set(self.fields):
            raise ValueError("natural_key must be a subset of fields")
        for value, name in (
            (self.row_limit, "row_limit"),
            (self.endpoint_budget_per_minute, "endpoint_budget_per_minute"),
            (self.daily_quota, "daily_quota"),
            (self.historical_retention_months, "historical_retention_months"),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when configured")

    def scopes(
        self, business_date: date | None, *, historical: bool = False
    ) -> Iterable[RequestScope]:
        builder = (
            self.historical_scope_builder
            if historical and self.historical_scope_builder is not None
            else self.scope_builder
        )
        return builder(business_date)


@dataclass(frozen=True, slots=True)
class DatasetDependencySpec:
    kind: DependencyKind
    name: str
    scope: ReleaseScope
    triggers_recompute: bool = True
    merge_previous_scopes: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("dependency name is required")


@dataclass(frozen=True, slots=True)
class QualityRuleSpec:
    name: str
    parameters: Mapping[str, ParameterValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("quality rule name is required")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    dataset_name: str
    processor: str
    processor_version: str
    dependencies: tuple[DatasetDependencySpec, ...]
    write_strategy: WriteStrategy
    release_scope: ReleaseScope
    quality_rules: tuple[QualityRuleSpec, ...]
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if not self.dataset_name or not self.processor or not self.processor_version:
            raise ValueError("dataset_name, processor, and processor_version are required")
        if not self.dependencies:
            raise ValueError("every dataset must declare at least one dependency")
        if len({(item.kind, item.name, item.scope) for item in self.dependencies}) != len(
            self.dependencies
        ):
            raise ValueError("dataset dependencies must be unique")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")


class SpecRegistry[SpecT: (ApiSpec, DatasetSpec)]:
    def __init__(self, key: Callable[[SpecT], str]) -> None:
        self._key: Callable[[SpecT], str] = key
        self._specs: dict[str, SpecT] = {}

    def register(self, spec: SpecT) -> None:
        name = self._key(spec)
        if name in self._specs:
            raise ValueError(f"spec {name!r} is already registered")
        self._specs[name] = spec

    def get(self, name: str) -> SpecT:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KeyError(f"unknown spec {name!r}") from exc

    def all(self) -> tuple[SpecT, ...]:
        return tuple(self._specs.values())
