from datetime import date
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.tushare import build_tushare_api_registry
from app.modules.acquisition.models import BatchType
from app.modules.operations.command_service import OperationCommandService
from app.modules.operations.models import DeferredCollectionStage


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


@pytest.mark.asyncio
async def test_repair_defers_both_dynamic_member_apis_until_their_masters_publish() -> None:
    service, session = _service((), (), ())
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

    assert {spec.api_name for spec in planned} == {"dc_concept", "ths_index"}
    assert deferred_count == 2
    stages = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], DeferredCollectionStage)
    ]
    assert {stage.api_name for stage in stages} == {"dc_concept_cons", "ths_member"}
    assert {stage.batch_type for stage in stages} == {BatchType.REPAIR.value}


@pytest.mark.asyncio
async def test_dynamic_member_apis_run_immediately_when_published_masters_are_reused() -> None:
    service, session = _service(("DC001",), ("885001.TI",), ("700001.TI",))
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
async def test_backfill_waits_for_fresh_theme_master_selected_in_the_same_command() -> None:
    service, session = _service(("OLD001",))
    specs = tuple(
        service._api_specs.get(api_name) for api_name in ("dc_concept", "dc_concept_cons")
    )

    planned, deferred_count = await service._manual_batch_specs(
        specs,
        business_date=date(2026, 7, 17),
        batch_type=BatchType.BACKFILL,
        command_id=uuid4(),
    )

    assert [spec.api_name for spec in planned] == ["dc_concept"]
    assert deferred_count == 1
    stage = session.add.call_args.args[0]
    assert isinstance(stage, DeferredCollectionStage)
    assert stage.api_name == "dc_concept_cons"
    assert stage.batch_type == BatchType.BACKFILL.value
