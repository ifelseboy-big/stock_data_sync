from datetime import UTC, date, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.tushare import build_tushare_api_registry
from app.modules.acquisition.models import BatchType
from app.modules.operations.command_service import (
    OperationCommandService,
    _is_unchanged_deterministic_failure,
)
from app.modules.operations.models import DeferredCollectionStage
from app.modules.processing.models import ProcessingTask, ProcessingTaskStatus


def _service(*scalar_results: tuple[str, ...]) -> tuple[OperationCommandService, MagicMock]:
    session = MagicMock()
    session.scalars = AsyncMock(side_effect=scalar_results)
    return (
        OperationCommandService(
            cast(AsyncSession, session),
            build_tushare_api_registry(),
        ),
        session,
    )


def _failed_stock_daily_task(*, process_type: str, attempt_count: int = 1) -> ProcessingTask:
    return ProcessingTask(
        process_id=uuid4(),
        source_batch_id=uuid4(),
        process_type=process_type,
        business_date=date(2026, 7, 17),
        output_dataset="stock_daily.core",
        output_version=uuid4(),
        status=ProcessingTaskStatus.FAILED.value,
        priority=100,
        attempt_count=attempt_count,
        max_attempts=3,
    )


def test_unchanged_deterministic_processing_failure_is_not_requeued() -> None:
    unchanged = _failed_stock_daily_task(process_type="stock_daily_core@4")
    prior_processor = _failed_stock_daily_task(process_type="stock_daily_core@3")
    exhausted = _failed_stock_daily_task(process_type="stock_daily_core@4", attempt_count=3)

    assert _is_unchanged_deterministic_failure(unchanged)
    assert not _is_unchanged_deterministic_failure(prior_processor)
    assert not _is_unchanged_deterministic_failure(exhausted)


@pytest.mark.asyncio
async def test_processing_retry_creates_current_version_without_mutating_old_task() -> None:
    task = _failed_stock_daily_task(process_type="stock_daily_core@3")
    replacement = ProcessingTask(
        process_id=uuid4(),
        source_batch_id=task.source_batch_id,
        process_type="stock_daily_core@4",
        business_date=task.business_date,
        output_dataset=task.output_dataset,
        output_version=uuid4(),
        status=ProcessingTaskStatus.WAITING_DEPENDENCY.value,
        priority=task.priority,
        attempt_count=0,
        max_attempts=3,
    )
    insert_result = MagicMock()
    insert_result.scalar_one_or_none.return_value = replacement.process_id
    session = MagicMock()
    session.execute = AsyncMock(return_value=insert_result)
    session.scalar = AsyncMock(side_effect=(replacement, 0))
    session.scalars = AsyncMock(return_value=())
    service = OperationCommandService(
        cast(AsyncSession, session),
        build_tushare_api_registry(),
    )

    queued = await service._queue_processing_task(task, datetime.now(UTC))

    assert task.process_type == "stock_daily_core@3"
    assert task.status == ProcessingTaskStatus.FAILED.value
    assert queued.process_id != task.process_id
    assert queued.output_version != task.output_version
    assert queued.process_type == "stock_daily_core@4"
    assert queued.status == ProcessingTaskStatus.QUEUED.value


@pytest.mark.asyncio
async def test_repair_only_defers_ths_members_until_their_master_publishes() -> None:
    service, session = _service((), ())
    specs = tuple(
        service._api_specs.get(api_name)
        for api_name in ("dc_concept", "dc_concept_cons", "ths_index", "ths_member")
    )

    planned, deferred_count = await service._manual_batch_specs(
        specs,
        business_date=date(2026, 7, 17),
        batch_type=BatchType.REPAIR,
        command_id=uuid4(),
    )

    assert {spec.api_name for spec in planned} == {
        "dc_concept",
        "dc_concept_cons",
        "ths_index",
    }
    assert deferred_count == 1
    stages = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], DeferredCollectionStage)
    ]
    assert {stage.api_name for stage in stages} == {"ths_member"}
    assert {stage.batch_type for stage in stages} == {BatchType.REPAIR.value}


@pytest.mark.asyncio
async def test_member_apis_run_immediately_when_ths_master_is_reused() -> None:
    service, session = _service(("885001.TI",), ("700001.TI",))
    specs = tuple(
        service._api_specs.get(api_name) for api_name in ("dc_concept_cons", "ths_member")
    )

    planned, deferred_count = await service._manual_batch_specs(
        specs,
        business_date=date(2026, 7, 17),
        batch_type=BatchType.REPAIR,
        command_id=uuid4(),
    )

    assert {spec.api_name for spec in planned} == {"dc_concept_cons", "ths_member"}
    assert deferred_count == 0
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_collects_theme_master_and_paginated_members_together() -> None:
    service, session = _service()
    specs = tuple(
        service._api_specs.get(api_name) for api_name in ("dc_concept", "dc_concept_cons")
    )

    planned, deferred_count = await service._manual_batch_specs(
        specs,
        business_date=date(2026, 7, 17),
        batch_type=BatchType.BACKFILL,
        command_id=uuid4(),
    )

    assert [spec.api_name for spec in planned] == ["dc_concept", "dc_concept_cons"]
    assert deferred_count == 0
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_manual_hot_scope_uses_historical_provider_mode() -> None:
    service, _session = _service()
    spec = service._api_specs.get("ths_hot")

    historical = await service._resolve_scopes(
        spec,
        date(2026, 1, 9),
        batch_type=BatchType.REPAIR,
    )
    current = await service._resolve_scopes(
        spec,
        date(2026, 1, 9),
        batch_type=BatchType.HOT,
    )

    assert historical[0].scope_key.endswith("is_new=N")
    assert historical[0].params["is_new"] == "N"
    assert current[0].scope_key.endswith("is_new=Y")
    assert current[0].params["is_new"] == "Y"
