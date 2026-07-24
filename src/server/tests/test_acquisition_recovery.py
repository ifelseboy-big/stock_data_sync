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
from app.modules.acquisition.domain import RunningTaskSnapshot, TaskTransition
from app.modules.acquisition.models import CollectionTaskStatus
from app.modules.acquisition.recovery import AcquisitionRecovery
from app.storage import LocalRawAssetStore, RawAssetContext

TIMEZONE = ZoneInfo("Asia/Shanghai")
SCHEMA = pa.schema((pa.field("trade_date", pa.string()),))


class FakeRepository:
    def __init__(self, task: RunningTaskSnapshot) -> None:
        self.task = task
        self.completed = False
        self.failed = False
        self.recovered = False
        self.full_asset_scan_called = False
        self.scoped_asset_task_ids: tuple[object, ...] = ()

    def assets(self) -> tuple[object, ...]:
        self.full_asset_scan_called = True
        return ()

    def assets_for_tasks(self, task_ids: tuple[object, ...]) -> tuple[object, ...]:
        self.scoped_asset_task_ids = task_ids
        return ()

    def running_tasks(self) -> tuple[RunningTaskSnapshot, ...]:
        return (self.task,)

    def complete_task(self, task: Any, metadata: Any, **values: Any) -> TaskTransition:
        self.completed = True
        return TaskTransition(task.task_id, CollectionTaskStatus.SUCCESS, None)

    def fail_task(self, task: Any, **values: Any) -> TaskTransition:
        self.failed = True
        return TaskTransition(task.task_id, CollectionTaskStatus.RETRY_WAIT, values["retry_at"])

    def recover_interrupted_task(self, task: Any, **values: Any) -> TaskTransition:
        self.recovered = True
        return TaskTransition(task.task_id, CollectionTaskStatus.RETRY_WAIT, values["now"])

    def mark_asset_missing(self, task_id: Any, **values: Any) -> None:
        raise AssertionError("no registered asset should be checked in this test")


def _spec() -> ApiSpec:
    return ApiSpec(
        api_name="daily",
        provider="TUSHARE",
        fields=("trade_date",),
        schema=SCHEMA,
        natural_key=("trade_date",),
        schedule_group=ScheduleGroup.DAILY,
        scope_builder=lambda business_date: (RequestScope("date", {"trade_date": business_date}),),
        split_policy=SplitPolicy.TRADE_DATE,
        row_limit=6_000,
        empty_policy=EmptyPolicy.ALLOWED,
        retry_policy=RetryPolicy(),
        date_extractor=lambda record: None,
    )


def _task() -> RunningTaskSnapshot:
    return RunningTaskSnapshot(
        task_id=uuid4(),
        batch_id=uuid4(),
        business_date=date(2026, 7, 17),
        provider="TUSHARE",
        api_name="daily",
        request_params={"trade_date": "20260717"},
        attempt_count=1,
        max_attempts=3,
        started_at=datetime(2026, 7, 17, 9, 0, tzinfo=TIMEZONE),
        execution_token=uuid4(),
    )


def test_startup_recovery_registers_sealed_orphan_without_refetch(tmp_path: Path) -> None:
    task = _task()
    repository = FakeRepository(task)
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
        (),
    )
    registry = SpecRegistry[ApiSpec](lambda item: item.api_name)
    registry.register(_spec())
    recovery = AcquisitionRecovery(
        repository=repository,  # type: ignore[arg-type]
        asset_store=store,
        api_specs=registry,
        timezone=TIMEZONE,
        running_timeout_seconds=300,
    )

    report = recovery.reconcile(recover_all_running=True)

    assert report.completed_tasks == 1
    assert repository.completed
    assert not repository.failed
    assert not repository.recovered


def test_startup_recovery_requeues_an_interrupted_final_attempt(tmp_path: Path) -> None:
    task = _task()
    task = RunningTaskSnapshot(
        task_id=task.task_id,
        batch_id=task.batch_id,
        business_date=task.business_date,
        provider=task.provider,
        api_name=task.api_name,
        request_params=task.request_params,
        attempt_count=task.max_attempts,
        max_attempts=task.max_attempts,
        started_at=task.started_at,
        execution_token=task.execution_token,
    )
    repository = FakeRepository(task)
    registry = SpecRegistry[ApiSpec](lambda item: item.api_name)
    registry.register(_spec())
    recovery = AcquisitionRecovery(
        repository=repository,  # type: ignore[arg-type]
        asset_store=LocalRawAssetStore(tmp_path),
        api_specs=registry,
        timezone=TIMEZONE,
        running_timeout_seconds=300,
    )

    report = recovery.reconcile(recover_all_running=True)

    assert report.retried_tasks == 1
    assert repository.recovered
    assert not repository.failed


def test_fast_startup_recovery_only_checks_running_tasks(tmp_path: Path) -> None:
    task = _task()
    repository = FakeRepository(task)
    registry = SpecRegistry[ApiSpec](lambda item: item.api_name)
    registry.register(_spec())
    store = LocalRawAssetStore(tmp_path)
    recovery = AcquisitionRecovery(
        repository=repository,  # type: ignore[arg-type]
        asset_store=store,
        api_specs=registry,
        timezone=TIMEZONE,
        running_timeout_seconds=300,
    )

    report = recovery.reconcile(
        recover_all_running=True,
        audit_all_assets=False,
    )

    assert report.retried_tasks == 1
    assert repository.recovered
    assert not repository.full_asset_scan_called
    assert repository.scoped_asset_task_ids == (task.task_id,)
    assert report.missing_assets == 0
    assert report.removed_temporary_files == 0
