from calendar import monthrange
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa

from app.catalog import ApiSpec, EmptyPolicy, SpecRegistry, SplitPolicy
from app.common.errors import CollectionError, ProviderError
from app.integrations.market_data.base import MarketDataProvider
from app.modules.acquisition.domain import ClaimedCollectionTask, TaskTransition
from app.modules.acquisition.models import BatchType
from app.modules.acquisition.repository import AcquisitionRepository
from app.observability.provider_calls import collection_task_context
from app.storage import LocalRawAssetStore, RawAssetContext, schema_fingerprint


@dataclass(slots=True)
class QueryStats:
    request_count: int = 0
    row_count: int = 0


class CollectionExecutor:
    def __init__(
        self,
        *,
        repository: AcquisitionRepository,
        provider: MarketDataProvider,
        api_specs: SpecRegistry[ApiSpec],
        asset_store: LocalRawAssetStore,
        timezone: ZoneInfo,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._api_specs = api_specs
        self._asset_store = asset_store
        self._timezone = timezone

    def execute(self, task: ClaimedCollectionTask) -> TaskTransition:
        with collection_task_context(task.task_id):
            return self._execute(task)

    def _execute(self, task: ClaimedCollectionTask) -> TaskTransition:
        now = datetime.now(self._timezone)
        spec = self._api_specs.get(task.api_name)
        stats = QueryStats()
        context = RawAssetContext(
            provider=task.provider,
            api_name=task.api_name,
            business_date=task.business_date,
            batch_id=task.batch_id,
            task_id=task.task_id,
        )
        try:
            expected_uri = self._asset_store.expected_uri(context)
            if self._asset_store.exists(expected_uri):
                metadata = self._asset_store.inspect(expected_uri)
                if metadata.schema_fingerprint != schema_fingerprint(spec.schema):
                    raise CollectionError(
                        "existing raw asset does not match the current ApiSpec schema",
                        error_code="SCHEMA_CHANGED",
                        retryable=False,
                    )
                return self._repository.complete_task(
                    task,
                    metadata,
                    request_count=0,
                    empty=metadata.row_count == 0,
                    completed_at=now,
                )

            first_page = self._query_page(spec, task.request_params, offset=0)
            stats.request_count += first_page.request_count
            first_table = first_page.table

            if first_table.num_rows == 0:
                self._validate_table(spec, task, first_table, set())
                return self._handle_empty(task, spec, stats, now)

            tables = self._table_stream(task, spec, first_table, stats)
            metadata = self._asset_store.seal(
                context,
                spec.schema,
                tables,
            )
            return self._repository.complete_task(
                task,
                metadata,
                request_count=stats.request_count,
                empty=False,
                completed_at=datetime.now(self._timezone),
            )
        except ProviderError as exc:
            stats.request_count += exc.request_count
            return self._record_failure(
                task,
                spec,
                error_code=exc.error_code,
                message=str(exc),
                retryable=exc.retryable,
                request_count=stats.request_count,
            )
        except CollectionError as exc:
            return self._record_failure(
                task,
                spec,
                error_code=exc.error_code,
                message=str(exc),
                retryable=exc.retryable,
                request_count=stats.request_count,
            )
        except Exception as exc:
            return self._record_failure(
                task,
                spec,
                error_code="ASSET_WRITE_ERROR",
                message=str(exc),
                retryable=True,
                request_count=stats.request_count,
            )

    def _table_stream(
        self,
        task: ClaimedCollectionTask,
        spec: ApiSpec,
        first_table: pa.Table,
        stats: QueryStats,
    ) -> Iterator[pa.Table]:
        seen_keys: set[tuple[object, ...]] = set()
        self._validate_table(spec, task, first_table, seen_keys)
        stats.row_count += first_table.num_rows
        yield first_table

        if spec.split_policy != SplitPolicy.OFFSET:
            if spec.row_limit is not None and first_table.num_rows >= spec.row_limit:
                raise CollectionError(
                    f"{spec.api_name} returned its row limit without a pagination strategy",
                    error_code="RESULT_MAY_BE_TRUNCATED",
                    retryable=False,
                )
            self._validate_expected_rows(spec, task.request_params, stats.row_count)
            return

        if spec.row_limit is None:
            raise CollectionError(
                f"{spec.api_name} OFFSET pagination requires row_limit",
                error_code="INVALID_API_SPEC",
                retryable=False,
            )

        offset = first_table.num_rows
        while first_table.num_rows == spec.row_limit:
            page = self._query_page(spec, task.request_params, offset=offset)
            stats.request_count += page.request_count
            table = page.table
            if table.num_rows == 0:
                break
            self._validate_table(spec, task, table, seen_keys)
            stats.row_count += table.num_rows
            yield table
            offset += table.num_rows
            first_table = table
        self._validate_expected_rows(spec, task.request_params, stats.row_count)

    def _query_page(
        self,
        spec: ApiSpec,
        request_params: Mapping[str, Any],
        *,
        offset: int,
    ) -> Any:
        params = dict(request_params)
        if spec.split_policy == SplitPolicy.OFFSET:
            if spec.row_limit is None:
                raise CollectionError(
                    "OFFSET pagination requires row_limit",
                    error_code="INVALID_API_SPEC",
                    retryable=False,
                )
            params["limit"] = spec.row_limit
            params["offset"] = offset
        return self._provider.query(
            spec.api_name,
            fields=spec.fields,
            schema=spec.schema,
            endpoint_budget_per_minute=spec.endpoint_budget_per_minute,
            **params,
        )

    def _validate_table(
        self,
        spec: ApiSpec,
        task: ClaimedCollectionTask,
        table: pa.Table,
        seen_keys: set[tuple[object, ...]],
    ) -> None:
        if not table.schema.equals(spec.schema, check_metadata=True):
            raise CollectionError(
                f"{spec.api_name} schema does not match ApiSpec",
                error_code="SCHEMA_CHANGED",
                retryable=False,
            )

        records = table.to_pylist()
        if task.business_date is not None:
            for record in records:
                extracted = spec.date_extractor(record)
                if extracted is not None and extracted != task.business_date:
                    raise CollectionError(
                        f"{spec.api_name} returned business date {extracted}",
                        error_code="BUSINESS_DATE_MISMATCH",
                        retryable=False,
                    )

        if spec.natural_key:
            for record in records:
                key = tuple(record[field] for field in spec.natural_key)
                if any(
                    value is None or (isinstance(value, float) and not isfinite(value))
                    for value in key
                ):
                    raise CollectionError(
                        f"{spec.api_name} returned an empty natural key",
                        error_code="INVALID_NATURAL_KEY",
                        retryable=False,
                    )
                if key in seen_keys:
                    raise CollectionError(
                        f"{spec.api_name} returned duplicate natural key {key!r}",
                        error_code="DUPLICATE_NATURAL_KEY",
                        retryable=False,
                    )
                seen_keys.add(key)

    @staticmethod
    def _validate_expected_rows(
        spec: ApiSpec,
        request_params: Mapping[str, Any],
        actual_rows: int,
    ) -> None:
        if spec.expected_row_count is None:
            return
        expected_rows = spec.expected_row_count(request_params)
        if expected_rows is not None and actual_rows != expected_rows:
            raise CollectionError(
                f"{spec.api_name} returned {actual_rows} rows; expected {expected_rows}",
                error_code="INCOMPLETE_RESULT",
                retryable=True,
            )

    def _handle_empty(
        self,
        task: ClaimedCollectionTask,
        spec: ApiSpec,
        stats: QueryStats,
        now: datetime,
    ) -> TaskTransition:
        historical_gap = spec.empty_policy != EmptyPolicy.UNSUPPORTED and (
            _outside_historical_retention(
                task,
                spec,
                today=now.date(),
            )
            or _exhausted_historical_backfill(task, today=now.date())
        )
        if spec.empty_policy == EmptyPolicy.ALLOWED or historical_gap:
            metadata = self._asset_store.seal(
                RawAssetContext(
                    provider=task.provider,
                    api_name=task.api_name,
                    business_date=task.business_date,
                    batch_id=task.batch_id,
                    task_id=task.task_id,
                ),
                spec.schema,
                (),
            )
            return self._repository.complete_task(
                task,
                metadata,
                request_count=stats.request_count,
                empty=True,
                completed_at=now,
                warning_message=(
                    _historical_gap_warning(task) if historical_gap else None
                ),
            )
        if spec.empty_policy == EmptyPolicy.UNSUPPORTED:
            return self._repository.fail_task(
                task.task_id,
                error_code="UNSUPPORTED",
                error_message="provider does not support this requested scope",
                request_count=stats.request_count,
                retry_at=None,
                completed_at=now,
                skipped=True,
            )
        retryable = spec.empty_policy == EmptyPolicy.RETRY_UNTIL_CUTOFF
        return self._record_failure(
            task,
            spec,
            error_code="EMPTY_RESULT",
            message="provider returned no rows for a required scope",
            retryable=retryable,
            request_count=stats.request_count,
        )

    def _record_failure(
        self,
        task: ClaimedCollectionTask,
        spec: ApiSpec,
        *,
        error_code: str,
        message: str,
        retryable: bool,
        request_count: int,
    ) -> TaskTransition:
        now = datetime.now(self._timezone)
        retry_at = None
        if retryable:
            wait_seconds = min(
                spec.retry_policy.initial_wait_seconds * (2 ** (task.attempt_count - 1)),
                spec.retry_policy.max_wait_seconds,
            )
            candidate = now + timedelta(seconds=wait_seconds)
            cutoff = _cutoff_at(
                business_date=task.business_date,
                cutoff_time=spec.retry_policy.cutoff_time,
                timezone=self._timezone,
            )
            current_day_waits_for_cutoff = (
                cutoff is not None
                and task.business_date == now.date()
                and now < cutoff
                and task.attempt_count >= task.max_attempts
            )
            if current_day_waits_for_cutoff:
                retry_at = cutoff
            elif task.attempt_count < task.max_attempts and (
                task.batch_type in {BatchType.BACKFILL, BatchType.REPAIR}
                or not _past_cutoff(
                    candidate,
                    business_date=task.business_date,
                    cutoff_time=spec.retry_policy.cutoff_time,
                    timezone=self._timezone,
                )
            ):
                retry_at = candidate
        return self._repository.fail_task(
            task.task_id,
            error_code=error_code,
            error_message=message,
            request_count=request_count,
            retry_at=retry_at,
            completed_at=now,
        )


def _past_cutoff(
    candidate: datetime,
    *,
    business_date: Any,
    cutoff_time: time | None,
    timezone: ZoneInfo,
) -> bool:
    cutoff = _cutoff_at(
        business_date=business_date,
        cutoff_time=cutoff_time,
        timezone=timezone,
    )
    if cutoff is None:
        return False
    return candidate > cutoff


def _cutoff_at(
    *,
    business_date: Any,
    cutoff_time: time | None,
    timezone: ZoneInfo,
) -> datetime | None:
    if cutoff_time is None or not isinstance(business_date, date):
        return None
    return datetime.combine(business_date, cutoff_time, timezone)


def _outside_historical_retention(
    task: ClaimedCollectionTask,
    spec: ApiSpec,
    *,
    today: date,
) -> bool:
    months = spec.historical_retention_months
    return (
        months is not None
        and task.batch_type in {BatchType.BACKFILL, BatchType.REPAIR}
        and task.business_date is not None
        and task.business_date < _months_before(today, months)
    )


def _exhausted_historical_backfill(
    task: ClaimedCollectionTask,
    *,
    today: date,
) -> bool:
    return (
        task.batch_type in {BatchType.BACKFILL, BatchType.REPAIR}
        and task.business_date is not None
        and task.business_date < today
        and task.attempt_count >= task.max_attempts
    )


def _historical_gap_warning(task: ClaimedCollectionTask) -> str:
    return (
        f"{task.api_name} 在历史数据周期 {task.business_date}、范围 {task.scope_key} "
        "未返回数据，已记录数据缺口并停止重试"
    )


def _months_before(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))
