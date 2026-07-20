from datetime import date, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pyarrow as pa
import pytest

from app.catalog import (
    ApiSpec,
    EmptyPolicy,
    RequestScope,
    RetryPolicy,
    ScheduleGroup,
    SplitPolicy,
)
from app.common.errors import CalendarCoverageError
from app.modules.acquisition.domain import BatchPlanResult
from app.modules.acquisition.models import BatchType
from app.modules.acquisition.planner import CollectionPlanner, StagePlan

TIMEZONE = ZoneInfo("Asia/Shanghai")


class FakeRepository:
    def __init__(self, is_open: bool | None) -> None:
        self.is_open = is_open
        self.batch_id = uuid4()
        self.blueprints: Any = None

    def is_trading_day(self, business_date: date) -> bool | None:
        return self.is_open

    def create_or_get_batch(self, **values: Any) -> Any:
        return self.batch_id

    def append_tasks(self, batch_id: Any, blueprints: Any, **values: Any) -> BatchPlanResult:
        self.blueprints = blueprints
        return BatchPlanResult(batch_id, len(blueprints), len(blueprints), True, "v1")


def _spec() -> ApiSpec:
    schema = pa.schema((pa.field("trade_date", pa.string()),))
    return ApiSpec(
        api_name="daily",
        provider="TUSHARE",
        fields=("trade_date",),
        schema=schema,
        natural_key=("trade_date",),
        schedule_group=ScheduleGroup.DAILY,
        scope_builder=lambda business_date: (
            RequestScope("market", {"trade_date": business_date}),
        ),
        split_policy=SplitPolicy.TRADE_DATE,
        row_limit=6_000,
        empty_policy=EmptyPolicy.RETRY_UNTIL_CUTOFF,
        retry_policy=RetryPolicy(),
        date_extractor=lambda record: None,
        historical_scope_builder=lambda business_date: (
            RequestScope("history", {"trade_date": business_date, "is_new": "N"}),
        ),
    )


def _stage() -> StagePlan:
    business_date = date(2026, 7, 17)
    return StagePlan(
        batch_type=BatchType.DAILY,
        business_date=business_date,
        scheduled_at=datetime(2026, 7, 17, 8, 45, tzinfo=TIMEZONE),
        api_specs=(_spec(),),
        finalize=True,
    )


def test_planner_skips_closed_trading_day_without_creating_batch() -> None:
    repository = FakeRepository(False)

    result = CollectionPlanner(repository).plan(  # type: ignore[arg-type]
        _stage(),
        now=datetime.now(TIMEZONE),
    )

    assert result.skipped_closed_day
    assert result.batch_id is None


def test_planner_requires_local_calendar_coverage() -> None:
    repository = FakeRepository(None)

    with pytest.raises(CalendarCoverageError):
        CollectionPlanner(repository).plan(  # type: ignore[arg-type]
            _stage(),
            now=datetime.now(TIMEZONE),
        )


def test_planner_converts_date_params_and_freezes_plan() -> None:
    repository = FakeRepository(True)

    result = CollectionPlanner(repository).plan(  # type: ignore[arg-type]
        _stage(),
        now=datetime.now(TIMEZONE),
    )

    assert result.plan is not None and result.plan.frozen
    assert repository.blueprints[0].request_params == {"trade_date": "20260717"}


def test_backfill_planner_uses_historical_scopes() -> None:
    repository = FakeRepository(True)
    stage = _stage()
    historical_stage = StagePlan(
        batch_type=BatchType.BACKFILL,
        business_date=stage.business_date,
        scheduled_at=stage.scheduled_at,
        api_specs=stage.api_specs,
        finalize=True,
    )

    CollectionPlanner(repository).plan(  # type: ignore[arg-type]
        historical_stage,
        now=datetime.now(TIMEZONE),
    )

    assert repository.blueprints[0].scope_key == "history"
    assert repository.blueprints[0].request_params == {
        "trade_date": "20260717",
        "is_new": "N",
    }
