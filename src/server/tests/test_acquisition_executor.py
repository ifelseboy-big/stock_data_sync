from datetime import date, datetime
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
        self.failures: list[dict[str, Any]] = []

    def complete_task(self, task: Any, metadata: Any, **values: Any) -> TaskTransition:
        self.completed_metadata = metadata
        status = (
            CollectionTaskStatus.EMPTY_VALID if values["empty"] else CollectionTaskStatus.SUCCESS
        )
        return TaskTransition(task.task_id, status, None)

    def fail_task(self, task_id: Any, **values: Any) -> TaskTransition:
        self.failures.append(values)
        retry_at = values["retry_at"]
        status = (
            CollectionTaskStatus.SKIPPED
            if values.get("skipped")
            else CollectionTaskStatus.RETRY_WAIT
            if retry_at is not None
            else CollectionTaskStatus.FAILED
        )
        return TaskTransition(task_id, status, retry_at)


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
        retry_policy=RetryPolicy(initial_wait_seconds=1, max_wait_seconds=10),
        date_extractor=lambda record: datetime.strptime(str(record["trade_date"]), "%Y%m%d").date(),
    )


def _task() -> ClaimedCollectionTask:
    return ClaimedCollectionTask(
        task_id=uuid4(),
        batch_id=uuid4(),
        batch_type=BatchType.DAILY,
        business_date=date(2026, 7, 17),
        provider="TUSHARE",
        api_name="daily",
        scope_key="trade_date=20260717",
        request_params={"trade_date": "20260717"},
        attempt_count=1,
        max_attempts=3,
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
