from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pyarrow as pa

from app.catalog import (
    ApiSpec,
    EmptyPolicy,
    RequestScope,
    RetryPolicy,
    ScheduleGroup,
    SpecRegistry,
    SplitPolicy,
)
from app.integrations.market_data.base import ProviderQueryResult
from app.modules.acquisition.domain import ClaimedCollectionTask, TaskTransition
from app.modules.acquisition.executor import CollectionExecutor
from app.modules.acquisition.models import BatchType, CollectionTaskStatus
from app.storage import LocalRawAssetStore, RawAssetContext

TIMEZONE = ZoneInfo("Asia/Shanghai")
SCHEMA = pa.schema(
    (
        pa.field("ts_code", pa.string()),
        pa.field("trade_date", pa.string()),
        pa.field("close", pa.float64()),
    )
)


class FakeProvider:
    name = "tushare"

    def __init__(self, tables: list[pa.Table]) -> None:
        self.tables = tables
        self.calls: list[dict[str, object]] = []

    def query(self, api_name: str, **params: object) -> ProviderQueryResult:
        self.calls.append({"api_name": api_name, **params})
        return ProviderQueryResult(table=self.tables.pop(0), request_count=1)


class FakeRepository:
    def __init__(self) -> None:
        self.completed_metadata: Any = None
        self.completed_values: dict[str, Any] = {}
        self.failures: list[dict[str, Any]] = []

    def complete_task(self, task: Any, metadata: Any, **values: Any) -> TaskTransition:
        self.completed_metadata = metadata
        self.completed_values = values
        status = (
            CollectionTaskStatus.EMPTY_VALID if values["empty"] else CollectionTaskStatus.SUCCESS
        )
        return TaskTransition(task.task_id, status, None)

    def fail_task(self, task: Any, **values: Any) -> TaskTransition:
        self.failures.append(values)
        retry_at = values["retry_at"]
        status = (
            CollectionTaskStatus.SKIPPED
            if values.get("skipped")
            else CollectionTaskStatus.RETRY_WAIT
            if retry_at is not None
            else CollectionTaskStatus.FAILED
        )
        return TaskTransition(task.task_id, status, retry_at)


def _table(rows: list[tuple[str, str, float]]) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {"ts_code": ts_code, "trade_date": trade_date, "close": close}
            for ts_code, trade_date, close in rows
        ],
        schema=SCHEMA,
    )


def _spec(
    *,
    split_policy: SplitPolicy = SplitPolicy.OFFSET,
    empty_policy: EmptyPolicy = EmptyPolicy.RETRY_UNTIL_CUTOFF,
    cutoff_time: time | None = None,
    historical_retention_months: int | None = None,
) -> ApiSpec:
    return ApiSpec(
        api_name="daily",
        provider="TUSHARE",
        fields=tuple(SCHEMA.names),
        schema=SCHEMA,
        natural_key=("ts_code", "trade_date"),
        schedule_group=ScheduleGroup.DAILY,
        scope_builder=lambda business_date: (
            RequestScope("trade_date", {"trade_date": business_date}),
        ),
        split_policy=split_policy,
        row_limit=2,
        empty_policy=empty_policy,
        retry_policy=RetryPolicy(
            initial_wait_seconds=1,
            max_wait_seconds=10,
            cutoff_time=cutoff_time,
        ),
        date_extractor=lambda record: datetime.strptime(str(record["trade_date"]), "%Y%m%d").date(),
        historical_retention_months=historical_retention_months,
    )


def _task(
    *,
    batch_type: BatchType = BatchType.DAILY,
    business_date: date = date(2026, 7, 17),
    attempt_count: int = 1,
    max_attempts: int = 3,
) -> ClaimedCollectionTask:
    return ClaimedCollectionTask(
        task_id=uuid4(),
        batch_id=uuid4(),
        batch_type=batch_type,
        business_date=business_date,
        provider="TUSHARE",
        api_name="daily",
        scope_key="trade_date=20260717",
        request_params={"trade_date": "20260717"},
        attempt_count=attempt_count,
        max_attempts=max_attempts,
    )


def _executor(
    tmp_path: Path,
    provider: FakeProvider,
    repository: FakeRepository,
    spec: ApiSpec,
) -> CollectionExecutor:
    registry = SpecRegistry[ApiSpec](lambda item: item.api_name)
    registry.register(spec)
    return CollectionExecutor(
        repository=repository,  # type: ignore[arg-type]
        provider=provider,
        api_specs=registry,
        asset_store=LocalRawAssetStore(tmp_path),
        timezone=TIMEZONE,
    )


def test_executor_streams_offset_pages_into_one_asset(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            _table([("000001.SZ", "20260717", 10.0), ("000002.SZ", "20260717", 20.0)]),
            _table([("000003.SZ", "20260717", 30.0)]),
        ]
    )
    repository = FakeRepository()

    transition = _executor(tmp_path, provider, repository, _spec()).execute(_task())

    assert transition.status == CollectionTaskStatus.SUCCESS
    assert repository.completed_metadata.row_count == 3
    assert [call["offset"] for call in provider.calls] == [0, 2]
    assert len(tuple(tmp_path.rglob("asset.parquet"))) == 1


def test_non_paginated_result_at_limit_is_not_sealed(tmp_path: Path) -> None:
    provider = FakeProvider(
        [_table([("000001.SZ", "20260717", 10.0), ("000002.SZ", "20260717", 20.0)])]
    )
    repository = FakeRepository()

    transition = _executor(
        tmp_path,
        provider,
        repository,
        _spec(split_policy=SplitPolicy.TRADE_DATE),
    ).execute(_task())

    assert transition.status == CollectionTaskStatus.FAILED
    assert repository.failures[0]["error_code"] == "RESULT_MAY_BE_TRUNCATED"
    assert tuple(tmp_path.rglob("asset.parquet")) == ()


def test_required_empty_result_enters_persistent_retry_wait(tmp_path: Path) -> None:
    provider = FakeProvider([_table([])])
    repository = FakeRepository()

    transition = _executor(tmp_path, provider, repository, _spec()).execute(_task())

    assert transition.status == CollectionTaskStatus.RETRY_WAIT
    assert transition.next_retry_at is not None
    assert tuple(tmp_path.rglob("asset.parquet")) == ()


def test_historical_backfill_retry_ignores_daily_cutoff(tmp_path: Path) -> None:
    provider = FakeProvider([_table([])])
    repository = FakeRepository()

    transition = _executor(
        tmp_path,
        provider,
        repository,
        _spec(cutoff_time=time(hour=0)),
    ).execute(_task(batch_type=BatchType.BACKFILL))

    assert transition.status == CollectionTaskStatus.RETRY_WAIT
    assert transition.next_retry_at is not None


def test_exhausted_historical_backfill_records_empty_gap_warning(tmp_path: Path) -> None:
    provider = FakeProvider([_table([])])
    repository = FakeRepository()

    transition = _executor(tmp_path, provider, repository, _spec()).execute(
        _task(
            batch_type=BatchType.BACKFILL,
            business_date=date(2025, 1, 20),
            attempt_count=3,
            max_attempts=3,
        )
    )

    assert transition.status == CollectionTaskStatus.EMPTY_VALID
    assert repository.completed_metadata.row_count == 0
    assert "已记录数据缺口并停止重试" in repository.completed_values["warning_message"]
    assert repository.failures == []


def test_allowed_empty_result_seals_zero_row_asset(tmp_path: Path) -> None:
    provider = FakeProvider([_table([])])
    repository = FakeRepository()

    transition = _executor(
        tmp_path,
        provider,
        repository,
        _spec(empty_policy=EmptyPolicy.ALLOWED),
    ).execute(_task())

    assert transition.status == CollectionTaskStatus.EMPTY_VALID
    assert repository.completed_metadata.row_count == 0
    assert repository.completed_values["warning_message"] is None


def test_backfill_empty_outside_provider_retention_is_valid(tmp_path: Path) -> None:
    provider = FakeProvider([])
    repository = FakeRepository()
    old_date = datetime.now(TIMEZONE).date() - timedelta(days=120)

    transition = _executor(
        tmp_path,
        provider,
        repository,
        _spec(historical_retention_months=3),
    ).execute(_task(batch_type=BatchType.BACKFILL, business_date=old_date))

    assert transition.status == CollectionTaskStatus.EMPTY_VALID
    assert repository.completed_metadata.row_count == 0
    assert "已记录数据缺口并停止重试" in repository.completed_values["warning_message"]
    assert provider.calls == []


def test_current_day_empty_waits_until_cutoff_after_attempt_budget(tmp_path: Path) -> None:
    provider = FakeProvider([_table([])])
    repository = FakeRepository()
    today = datetime.now(TIMEZONE).date()

    transition = _executor(
        tmp_path,
        provider,
        repository,
        _spec(cutoff_time=time.max),
    ).execute(
        _task(
            batch_type=BatchType.BACKFILL,
            business_date=today,
            attempt_count=3,
            max_attempts=3,
        )
    )

    assert transition.status == CollectionTaskStatus.RETRY_WAIT
    assert transition.next_retry_at is not None
    assert transition.next_retry_at.date() == today


def test_existing_orphan_asset_is_registered_without_provider_call(tmp_path: Path) -> None:
    task = _task()
    store = LocalRawAssetStore(tmp_path)
    store.seal(
        RawAssetContext(
            provider=task.provider,
            api_name=task.api_name,
            business_date=task.business_date,
            batch_id=task.batch_id,
            task_id=task.task_id,
            execution_token=task.execution_token,
        ),
        SCHEMA,
        (_table([("000001.SZ", "20260717", 10.0)]),),
    )
    provider = FakeProvider([])
    repository = FakeRepository()
    registry = SpecRegistry[ApiSpec](lambda item: item.api_name)
    registry.register(_spec())
    executor = CollectionExecutor(
        repository=repository,  # type: ignore[arg-type]
        provider=provider,
        api_specs=registry,
        asset_store=store,
        timezone=TIMEZONE,
    )

    transition = executor.execute(task)

    assert transition.status == CollectionTaskStatus.SUCCESS
    assert provider.calls == []
