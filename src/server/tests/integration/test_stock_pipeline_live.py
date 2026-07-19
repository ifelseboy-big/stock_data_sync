import os
from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.catalog import ApiSpec
from app.catalog.tushare import (
    DAILY_CLOSE_SPECS,
    DAILY_FINAL_SPECS,
    DAILY_LATE_SPECS,
    DAILY_PREOPEN_SPECS,
    MASTER_STOCK_SPECS,
    TRADE_CAL_SPEC,
)
from app.db.sync_session import SyncSessionFactory
from app.modules.acquisition.factory import (
    get_acquisition_repository,
    get_acquisition_runtime,
    get_collection_planner,
    shutdown_acquisition_runtime,
)
from app.modules.acquisition.models import BatchType
from app.modules.acquisition.planner import StagePlan
from app.modules.processing.factory import (
    get_dataset_specs,
    get_processing_repository,
    get_processing_runtime,
)
from app.modules.processing.models import DatasetRelease, ProcessingTaskStatus
from app.modules.stocks.models import (
    Stock,
    StockCompany,
    StockDaily,
    StockMoneyflowDaily,
    StockSuspendDaily,
    StockTechnicalDaily,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_TUSHARE_INTEGRATION") != "1",
    reason="requires calendar publication, isolated PostgreSQL, and configured Tushare token",
)

TIMEZONE = ZoneInfo("Asia/Shanghai")
BUSINESS_DATE = date(2026, 7, 17)


def test_stock_master_and_daily_pipeline() -> None:
    now = datetime.now(TIMEZONE)
    _collect_stage(
        BatchType.MASTER,
        date(2026, 7, 1),
        datetime(2026, 7, 1, 8, 21, tzinfo=TIMEZONE),
        (TRADE_CAL_SPEC,),
        now,
    )
    processing_repository = get_processing_repository()
    calendar_plan = processing_repository.plan_closed_batches(get_dataset_specs().all(), now=now)
    assert calendar_plan.created_task_count == 1
    _drain_processing(now)

    master_batch = _collect_stage(
        BatchType.MASTER,
        date(2026, 7, 1),
        datetime(2026, 7, 1, 8, 30, tzinfo=TIMEZONE),
        MASTER_STOCK_SPECS,
        now,
    )
    master_plan = processing_repository.plan_closed_batches(get_dataset_specs().all(), now=now)
    assert master_plan.created_task_count == 2
    _drain_processing(now)

    with SyncSessionFactory() as session:
        stock_count = session.scalar(select(func.count()).select_from(Stock))
        company_count = session.scalar(select(func.count()).select_from(StockCompany))
        stock_release = session.get(DatasetRelease, ("stock", "GLOBAL", "GLOBAL"))
        company_release = session.get(DatasetRelease, ("stock_company", "GLOBAL", "GLOBAL"))
    assert master_batch is not None
    assert stock_count is not None and stock_count > 5_000
    assert company_count is not None and company_count > 4_000
    assert stock_release is not None and stock_release.row_count == stock_count
    assert company_release is not None and company_release.row_count == company_count

    daily_specs = (
        *DAILY_PREOPEN_SPECS,
        *DAILY_CLOSE_SPECS,
        *DAILY_LATE_SPECS,
        *DAILY_FINAL_SPECS,
    )
    _collect_stage(
        BatchType.DAILY,
        BUSINESS_DATE,
        datetime.combine(BUSINESS_DATE, time(hour=8, minute=45), TIMEZONE),
        daily_specs,
        now,
    )
    daily_plan = processing_repository.plan_closed_batches(get_dataset_specs().all(), now=now)
    assert daily_plan.created_task_count == 5
    _drain_processing(now)

    with SyncSessionFactory() as session:
        core_count = session.scalar(
            select(func.count())
            .select_from(StockDaily)
            .where(StockDaily.trade_date == BUSINESS_DATE)
        )
        technical_count = session.scalar(
            select(func.count())
            .select_from(StockTechnicalDaily)
            .where(StockTechnicalDaily.trade_date == BUSINESS_DATE)
        )
        moneyflow_count = session.scalar(
            select(func.count())
            .select_from(StockMoneyflowDaily)
            .where(StockMoneyflowDaily.trade_date == BUSINESS_DATE)
        )
        suspend_count = session.scalar(
            select(func.count())
            .select_from(StockSuspendDaily)
            .where(StockSuspendDaily.trade_date == BUSINESS_DATE)
        )
        releases = set(
            session.scalars(
                select(DatasetRelease.dataset_name).where(
                    DatasetRelease.scope_type == "DATE",
                    DatasetRelease.scope_key == BUSINESS_DATE.isoformat(),
                )
            )
        )
    assert core_count is not None and core_count > 5_000
    assert technical_count is not None and technical_count >= core_count
    assert moneyflow_count is not None and moneyflow_count > 5_000
    assert suspend_count is not None and suspend_count >= 0
    assert releases == {
        "stock_daily.core",
        "stock_daily.limit",
        "stock_moneyflow_daily",
        "stock_suspend_daily",
        "stock_technical_daily",
    }


def _collect_stage(
    batch_type: BatchType,
    business_date: date,
    scheduled_at: datetime,
    api_specs: tuple[ApiSpec, ...],
    now: datetime,
) -> UUID:
    plan = get_collection_planner().plan(
        StagePlan(
            batch_type=batch_type,
            business_date=business_date,
            scheduled_at=scheduled_at,
            api_specs=api_specs,
            finalize=True,
        ),
        now=now,
    )
    while True:
        submitted = get_acquisition_runtime().dispatch(now=now)
        shutdown_acquisition_runtime()
        if submitted == 0:
            break
    closed = get_acquisition_repository().close_ready_batches(now=now)
    assert plan.batch_id in closed
    assert plan.batch_id is not None
    return plan.batch_id


def _drain_processing(now: datetime) -> None:
    while True:
        transition = get_processing_runtime().dispatch(now=now)
        if transition is None:
            break
        assert transition.status == ProcessingTaskStatus.SUCCESS
