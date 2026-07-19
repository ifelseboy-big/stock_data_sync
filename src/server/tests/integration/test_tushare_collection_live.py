import os
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.catalog.tushare import TRADE_CAL_SPEC
from app.db.sync_session import SyncSessionFactory
from app.modules.acquisition.domain import TERMINAL_TASK_STATUSES
from app.modules.acquisition.factory import (
    get_acquisition_repository,
    get_acquisition_runtime,
    get_collection_planner,
    shutdown_acquisition_runtime,
)
from app.modules.acquisition.models import (
    BatchType,
    CollectionTask,
    RawDataAsset,
)
from app.modules.acquisition.planner import StagePlan
from app.modules.processing.factory import (
    get_dataset_specs,
    get_processing_repository,
    get_processing_runtime,
)
from app.modules.processing.models import (
    DatasetRelease,
    ProcessingTask,
    ProcessingTaskStatus,
)
from app.modules.stocks.models import TradeCalendar

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_TUSHARE_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database and configured Tushare token",
)

TIMEZONE = ZoneInfo("Asia/Shanghai")


def test_trade_calendar_collection_closes_a_real_batch() -> None:
    now = datetime.now(TIMEZONE)
    business_date = date(now.year, now.month, 1)
    scheduled_at = datetime.combine(business_date, time(hour=8, minute=20), TIMEZONE)
    plan = get_collection_planner().plan(
        StagePlan(
            batch_type=BatchType.MASTER,
            business_date=business_date,
            scheduled_at=scheduled_at,
            api_specs=(TRADE_CAL_SPEC,),
            finalize=True,
        ),
        now=now,
    )
    assert plan.plan is not None and plan.plan.total_task_count == 2

    runtime = get_acquisition_runtime()
    assert runtime.dispatch(now=now) == 2
    shutdown_acquisition_runtime()

    repository = get_acquisition_repository()
    assert repository.close_ready_batches(now=datetime.now(TIMEZONE)) == (plan.batch_id,)

    with SyncSessionFactory() as session:
        statuses = tuple(
            session.scalars(
                select(CollectionTask.status).where(CollectionTask.batch_id == plan.batch_id)
            )
        )
        asset_count = session.scalar(
            select(func.count())
            .select_from(RawDataAsset)
            .join(CollectionTask, CollectionTask.task_id == RawDataAsset.task_id)
            .where(CollectionTask.batch_id == plan.batch_id)
        )

    assert len(statuses) == 2
    assert set(statuses) <= TERMINAL_TASK_STATUSES
    assert asset_count == 2

    processing_repository = get_processing_repository()
    processing_plan = processing_repository.plan_closed_batches(
        get_dataset_specs().all(),
        now=datetime.now(TIMEZONE),
    )
    assert processing_plan.created_task_count == 1

    transition = get_processing_runtime().dispatch(now=datetime.now(TIMEZONE))
    assert transition is not None
    assert transition.status == ProcessingTaskStatus.SUCCESS

    with SyncSessionFactory() as session:
        calendar_rows = session.scalar(select(func.count()).select_from(TradeCalendar))
        release = session.get(DatasetRelease, ("trade_calendar", "GLOBAL", "GLOBAL"))
        processing_task = session.scalar(
            select(ProcessingTask).where(ProcessingTask.source_batch_id == plan.batch_id)
        )

    assert calendar_rows in (730, 732)
    assert release is not None and release.row_count == calendar_rows
    assert release.business_date is None
    assert processing_task is not None
    assert processing_task.status == ProcessingTaskStatus.SUCCESS.value
