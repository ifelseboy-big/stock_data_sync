from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select

from app.catalog import ApiSpec
from app.catalog.tushare import (
    ALL_TUSHARE_API_SPECS,
    DAILY_CLOSE_SPECS,
    DAILY_LATE_SPECS,
    DAILY_PREOPEN_SPECS,
    DELAYED_ETF_SPECS,
    DELAYED_THEME_SPECS,
    HOT_SPECS,
    MASTER_ENTITY_SPECS,
    MASTER_ETF_SPECS,
    MASTER_SPECIAL_SPECS,
    MASTER_STOCK_SPECS,
    MONTHLY_INDEX_SPECS,
    TRADE_CAL_SPEC,
    next_year_trade_calendar_scopes,
    theme_member_scopes,
    ths_member_scopes,
)
from app.common.errors import CalendarCoverageError
from app.core.config import settings
from app.db.sync_session import SyncSessionFactory, sync_engine
from app.modules.acquisition.factory import (
    get_acquisition_recovery,
    get_acquisition_repository,
    get_acquisition_runtime,
    get_api_specs,
    get_collection_planner,
)
from app.modules.acquisition.models import BatchType
from app.modules.acquisition.planner import StagePlan
from app.modules.partitions.service import ensure_monthly_partitions
from app.modules.processing.factory import (
    get_dataset_specs,
    get_processing_repository,
    get_processing_runtime,
)
from app.modules.stocks.models import TradeCalendar
from app.modules.topics.models import ConceptBoard, MarketThemeDaily, ThemeIndex


def dispatch_collection_tasks() -> None:
    now = datetime.now(ZoneInfo(settings.scheduler_timezone))
    get_acquisition_runtime().dispatch(now=now)


def close_collection_batches() -> None:
    now = datetime.now(ZoneInfo(settings.scheduler_timezone))
    closed_ids = get_acquisition_repository().close_ready_batches(now=now)
    if closed_ids:
        structlog.get_logger("scheduler").info(
            "collection_batches_closed",
            batch_ids=[str(item) for item in closed_ids],
        )


def reconcile_collection_runtime(*, recover_all_running: bool = False) -> None:
    get_acquisition_recovery().reconcile(recover_all_running=recover_all_running)


def plan_processing_tasks() -> None:
    now = datetime.now(ZoneInfo(settings.scheduler_timezone))
    result = get_processing_repository().plan_closed_batches(
        get_dataset_specs().all(),
        now=now,
    )
    if result.created_task_count or result.queued_task_count or result.blocked_task_count:
        structlog.get_logger("scheduler").info(
            "processing_tasks_planned",
            scanned_batch_count=result.scanned_batch_count,
            created_task_count=result.created_task_count,
            queued_task_count=result.queued_task_count,
            blocked_task_count=result.blocked_task_count,
        )


def dispatch_processing_task() -> None:
    now = datetime.now(ZoneInfo(settings.scheduler_timezone))
    get_processing_runtime().dispatch(now=now)


def reconcile_processing_runtime(*, recover_all_running: bool = False) -> None:
    now = datetime.now(ZoneInfo(settings.scheduler_timezone))
    started_before = (
        None
        if recover_all_running
        else now - timedelta(seconds=settings.processing_running_timeout_seconds)
    )
    recovered_count = get_processing_repository().recover_running_tasks(
        now=now,
        started_before=started_before,
    )
    if recovered_count:
        structlog.get_logger("scheduler").warning(
            "processing_tasks_recovered",
            recovered_count=recovered_count,
        )


def plan_trade_calendar() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    business_date = date(now.year, now.month, 1)
    scheduled_at = datetime.combine(business_date, time(hour=8, minute=20), timezone)
    spec = get_api_specs().get("trade_cal")
    _plan_stage(
        StagePlan(
            batch_type=BatchType.MASTER,
            business_date=business_date,
            scheduled_at=scheduled_at,
            api_specs=(spec,),
            finalize=True,
        ),
        now=now,
        stage_name="trade_calendar",
    )


def plan_next_year_trade_calendar() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    business_date = date(now.year, now.month, 1)
    scheduled_at = datetime.combine(business_date, time(hour=8, minute=25), timezone)
    next_year_spec = replace(
        TRADE_CAL_SPEC,
        scope_builder=next_year_trade_calendar_scopes,
    )
    _plan_stage(
        StagePlan(
            batch_type=BatchType.MASTER,
            business_date=business_date,
            scheduled_at=scheduled_at,
            api_specs=(next_year_spec,),
            finalize=True,
        ),
        now=now,
        stage_name="next_year_trade_calendar",
    )


def plan_stock_master() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    business_date = date(now.year, now.month, 1)
    scheduled_at = datetime.combine(business_date, time(hour=8, minute=30), timezone)
    _plan_stage(
        StagePlan(
            batch_type=BatchType.MASTER,
            business_date=business_date,
            scheduled_at=scheduled_at,
            api_specs=MASTER_STOCK_SPECS,
            finalize=True,
        ),
        now=now,
        stage_name="stock_master",
    )


def plan_etf_master() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    business_date = date(now.year, now.month, 1)
    scheduled_at = datetime.combine(business_date, time(hour=8, minute=35), timezone)
    _plan_stage(
        StagePlan(
            batch_type=BatchType.MASTER,
            business_date=business_date,
            scheduled_at=scheduled_at,
            api_specs=MASTER_ETF_SPECS,
            finalize=True,
        ),
        now=now,
        stage_name="etf_master",
    )


def plan_special_master() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    business_date = date(now.year, now.month, 1)
    scheduled_at = datetime.combine(business_date, time(hour=8, minute=40), timezone)
    _plan_stage(
        StagePlan(
            batch_type=BatchType.MASTER,
            business_date=business_date,
            scheduled_at=scheduled_at,
            api_specs=MASTER_SPECIAL_SPECS,
            finalize=True,
        ),
        now=now,
        stage_name="special_master",
    )


def plan_ths_board_members() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    business_date = date(now.year, now.month, 1)
    with SyncSessionFactory() as session:
        concept_codes = tuple(
            session.scalars(
                select(ConceptBoard.ts_code)
                .where(ConceptBoard.source == "THS")
                .order_by(ConceptBoard.ts_code)
            )
        )
        theme_codes = tuple(
            session.scalars(
                select(ThemeIndex.ts_code)
                .where(ThemeIndex.source == "THS")
                .order_by(ThemeIndex.ts_code)
            )
        )
    codes = tuple(sorted({*concept_codes, *theme_codes}))
    if not codes:
        structlog.get_logger("scheduler").warning("ths_member_waiting_for_master")
        return
    dynamic_spec = replace(
        MASTER_ENTITY_SPECS[0],
        scope_builder=lambda ignored_date: ths_member_scopes(codes),
    )
    _plan_stage(
        StagePlan(
            batch_type=BatchType.MASTER,
            business_date=business_date,
            scheduled_at=datetime.combine(business_date, time(hour=10), timezone),
            api_specs=(dynamic_spec,),
            finalize=True,
        ),
        now=now,
        stage_name="ths_board_members",
    )


def plan_monthly_index_weights() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    current_month = date(now.year, now.month, 1)
    target_month = (current_month - timedelta(days=1)).replace(day=1)
    scheduled_at = datetime.combine(date(now.year, now.month, 2), time(hour=8, minute=50), timezone)
    _plan_stage(
        StagePlan(
            batch_type=BatchType.MASTER,
            business_date=target_month,
            scheduled_at=scheduled_at,
            api_specs=MONTHLY_INDEX_SPECS,
            finalize=True,
        ),
        now=now,
        stage_name="monthly_index_weights",
    )


def plan_etf_share_size() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    with SyncSessionFactory() as session:
        business_date = session.scalar(
            select(TradeCalendar.cal_date)
            .where(
                TradeCalendar.exchange == "SSE",
                TradeCalendar.cal_date < now.date(),
                TradeCalendar.is_open.is_(True),
            )
            .order_by(TradeCalendar.cal_date.desc())
            .limit(1)
        )
    if business_date is None:
        raise CalendarCoverageError("trade calendar has no previous SSE trading day")
    scheduled_at = datetime.combine(now.date(), time(hour=8, minute=45), timezone)
    _plan_stage(
        StagePlan(
            batch_type=BatchType.DELAYED,
            business_date=business_date,
            scheduled_at=scheduled_at,
            api_specs=DELAYED_ETF_SPECS,
            finalize=True,
        ),
        now=now,
        stage_name="etf_share_size",
    )


def plan_daily_preopen() -> None:
    _plan_daily_stage(DAILY_PREOPEN_SPECS, finalize=False, stage_name="daily_preopen")


def plan_daily_close() -> None:
    _plan_daily_stage(DAILY_CLOSE_SPECS, finalize=False, stage_name="daily_close")


def plan_daily_late() -> None:
    _plan_daily_stage(DAILY_LATE_SPECS, finalize=False, stage_name="daily_late")


def plan_daily_final() -> None:
    daily_specs = tuple(
        spec for spec in ALL_TUSHARE_API_SPECS if spec.schedule_group.value == "DAILY"
    )
    _plan_daily_stage(daily_specs, finalize=True, stage_name="daily_final")


def plan_theme_members() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    business_date = now.date()
    with SyncSessionFactory() as session:
        theme_codes = tuple(
            session.scalars(
                select(MarketThemeDaily.theme_code)
                .where(
                    MarketThemeDaily.source == "DC",
                    MarketThemeDaily.trade_date == business_date,
                )
                .order_by(MarketThemeDaily.theme_code)
            )
        )
    if not theme_codes:
        structlog.get_logger("scheduler").info(
            "theme_member_waiting_for_daily_theme",
            business_date=business_date.isoformat(),
        )
        return
    dynamic_spec = replace(
        DELAYED_THEME_SPECS[0],
        scope_builder=lambda ignored_date: theme_member_scopes(business_date, theme_codes),
    )
    _plan_stage(
        StagePlan(
            batch_type=BatchType.DELAYED,
            business_date=business_date,
            scheduled_at=datetime.combine(business_date, time(hour=20), timezone),
            api_specs=(dynamic_spec,),
            finalize=True,
        ),
        now=now,
        stage_name="theme_members",
    )


def plan_hot_rank() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    business_date = now.date()
    _plan_stage(
        StagePlan(
            batch_type=BatchType.HOT,
            business_date=business_date,
            scheduled_at=datetime.combine(business_date, time(hour=22, minute=35), timezone),
            api_specs=HOT_SPECS,
            finalize=True,
        ),
        now=now,
        stage_name="hot_rank",
    )


def plan_due_collection_stages() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    stages = (
        (time(hour=8, minute=45), plan_etf_share_size),
        (time(hour=9, minute=25), plan_daily_preopen),
        (time(hour=16, minute=10), plan_daily_close),
        (time(hour=17, minute=30), plan_daily_late),
        (time(hour=19), plan_daily_final),
        (time(hour=20), plan_theme_members),
        (time(hour=22, minute=35), plan_hot_rank),
    )
    for scheduled_time, plan_action in stages:
        if now.time() >= scheduled_time:
            try:
                plan_action()
            except CalendarCoverageError:
                structlog.get_logger("scheduler").warning(
                    "daily_stage_waiting_for_calendar",
                    stage=plan_action.__name__,
                    business_date=now.date().isoformat(),
                )
                break


def _plan_daily_stage(
    api_specs: tuple[ApiSpec, ...],
    *,
    finalize: bool,
    stage_name: str,
) -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    scheduled_at = datetime.combine(now.date(), time(hour=8, minute=45), timezone)
    _plan_stage(
        StagePlan(
            batch_type=BatchType.DAILY,
            business_date=now.date(),
            scheduled_at=scheduled_at,
            api_specs=api_specs,
            finalize=finalize,
        ),
        now=now,
        stage_name=stage_name,
    )


def _plan_stage(stage: StagePlan, *, now: datetime, stage_name: str) -> None:
    result = get_collection_planner().plan(stage, now=now)
    structlog.get_logger("scheduler").info(
        "collection_stage_planned",
        stage=stage_name,
        batch_id=str(result.batch_id) if result.batch_id else None,
        skipped_closed_day=result.skipped_closed_day,
        created_task_count=result.plan.created_task_count if result.plan else 0,
        total_task_count=result.plan.total_task_count if result.plan else 0,
        frozen=result.plan.frozen if result.plan else False,
    )


def ensure_future_partitions() -> None:
    reference_date = datetime.now(ZoneInfo(settings.scheduler_timezone)).date()
    with sync_engine.begin() as connection:
        partition_names = ensure_monthly_partitions(
            connection,
            reference_date=reference_date,
            months_ahead=settings.partition_months_ahead,
        )
    structlog.get_logger("scheduler").info(
        "partitions_checked",
        partition_count=len(partition_names),
        through_month=partition_names[-1][-6:] if partition_names else None,
    )
