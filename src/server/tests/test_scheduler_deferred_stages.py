from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.catalog.tushare import build_tushare_api_registry
from app.modules.acquisition.models import BatchType
from app.modules.operations.models import DeferredCollectionStage
from app.scheduler import jobs


def test_deferred_collection_stages_plan_both_dynamic_member_batches(monkeypatch) -> None:
    created_at = datetime(2026, 7, 20, 1, tzinfo=UTC)
    theme_stage = DeferredCollectionStage(
        stage_id=uuid4(),
        command_id=uuid4(),
        api_name="dc_concept_cons",
        business_date=date(2026, 7, 17),
        batch_type=BatchType.BACKFILL.value,
        status="PENDING",
        created_at=created_at,
    )
    ths_stage = DeferredCollectionStage(
        stage_id=uuid4(),
        command_id=uuid4(),
        api_name="ths_member",
        business_date=date(2026, 7, 17),
        batch_type=BatchType.REPAIR.value,
        status="PENDING",
        created_at=created_at,
    )
    session = MagicMock()
    stage_result = MagicMock()
    stage_result.all.return_value = (theme_stage, ths_stage)
    session.scalars.side_effect = [
        stage_result,
        ("concept_board", "theme_index"),
        ("885001.TI",),
        ("700001.TI",),
    ]
    session.scalar.return_value = uuid4()
    session_context = MagicMock()
    session_context.__enter__.return_value = session
    session.begin.return_value.__enter__.return_value = None
    session.begin.return_value.__exit__.return_value = False
    planner = MagicMock()
    theme_batch_id = uuid4()
    ths_batch_id = uuid4()
    planner.plan.side_effect = [
        SimpleNamespace(batch_id=theme_batch_id),
        SimpleNamespace(batch_id=ths_batch_id),
    ]

    monkeypatch.setattr(jobs, "SyncSessionFactory", lambda: session_context)
    monkeypatch.setattr(jobs, "get_collection_planner", lambda: planner)
    monkeypatch.setattr(jobs, "get_api_specs", build_tushare_api_registry)

    jobs.plan_deferred_collection_stages()

    assert theme_stage.status == "PLANNED"
    assert theme_stage.batch_id == theme_batch_id
    assert ths_stage.status == "PLANNED"
    assert ths_stage.batch_id == ths_batch_id
    theme_plan = planner.plan.call_args_list[0].args[0]
    ths_plan = planner.plan.call_args_list[1].args[0]
    assert theme_plan.batch_type == BatchType.BACKFILL
    assert [
        scope.scope_key for scope in theme_plan.api_specs[0].scope_builder(date(2026, 7, 17))
    ] == ["trade_date=20260717"]
    assert ths_plan.batch_type == BatchType.REPAIR
    assert [scope.scope_key for scope in ths_plan.api_specs[0].scope_builder(None)] == [
        "ts_code=700001.TI",
        "ts_code=885001.TI",
    ]
