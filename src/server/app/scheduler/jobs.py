from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult

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
    STOCK_BASIC_SPEC,
    TRADE_CAL_SPEC,
    next_year_trade_calendar_scopes,
    ths_member_scopes,
)
from app.common.errors import CalendarCoverageError, ClosedBatchPlanMismatchError
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
from app.modules.operations.models import DeferredCollectionStage, ScheduledJobExecution
from app.modules.partitions.service import ensure_monthly_partitions
from app.modules.processing.factory import (
    get_dataset_specs,
    get_processing_repository,
    get_processing_runtime,
)
from app.modules.processing.models import DatasetRelease
from app.modules.stocks.models import TradeCalendar
from app.modules.topics.models import ConceptBoard, ThemeIndex


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
    plan_deferred_collection_stages()


def plan_deferred_collection_stages() -> None:
    now = datetime.now(ZoneInfo(settings.scheduler_timezone))
    planned_batch_ids: list[str] = []
    completed_without_tasks = 0
    with SyncSessionFactory() as session, session.begin():
        stages = session.scalars(
            select(DeferredCollectionStage)
            .where(DeferredCollectionStage.status == "PENDING")
            .order_by(
                DeferredCollectionStage.created_at,
                DeferredCollectionStage.stage_id,
            )
            .with_for_update(skip_locked=True, of=DeferredCollectionStage)
            .limit(50)
        ).all()
        for stage in stages:
            if stage.api_name == "dc_concept_cons":
                stage_date = stage.business_date
                if stage_date is None:
                    raise RuntimeError("dc_concept_cons deferred stage has no business date")
                release_ready = session.scalar(
                    select(DatasetRelease.process_id).where(
                        DatasetRelease.dataset_name == "market_theme_daily",
                        DatasetRelease.business_date == stage_date,
                        DatasetRelease.published_at >= stage.created_at,
                    )
                )
                if release_ready is None:
                    continue
                dynamic_spec = get_api_specs().get(stage.api_name)
                scope_count = 1
            elif stage.api_name == "ths_member":
                ready_datasets = set(
                    session.scalars(
                        select(DatasetRelease.dataset_name).where(
                            DatasetRelease.dataset_name.in_(("concept_board", "theme_index")),
                            DatasetRelease.published_at >= stage.created_at,
                        )
                    )
                )
                if ready_datasets != {"concept_board", "theme_index"}:
                    continue
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
                ths_codes = tuple(sorted({*concept_codes, *theme_codes}))
                dynamic_spec = _ths_member_spec(stage.api_name, ths_codes)
                scope_count = len(ths_codes)
            else:
                raise RuntimeError(f"unsupported deferred collection API: {stage.api_name}")
            if not scope_count:
                stage.status = "PLANNED"
                stage.planned_at = now
                completed_without_tasks += 1
                continue
            result = get_collection_planner().plan(
                StagePlan(
                    batch_type=BatchType(stage.batch_type),
                    business_date=stage.business_date,
                    scheduled_at=stage.created_at
                    + timedelta(microseconds=1 if stage.api_name == "dc_concept_cons" else 2),
                    api_specs=(dynamic_spec,),
                    finalize=True,
                ),
                now=now,
            )
            if result.batch_id is None:
                raise RuntimeError("deferred collection stage unexpectedly skipped")
            stage.status = "PLANNED"
            stage.batch_id = result.batch_id
            stage.planned_at = now
            planned_batch_ids.append(str(result.batch_id))
    if planned_batch_ids or completed_without_tasks:
        structlog.get_logger("scheduler").info(
            "deferred_collection_stages_planned",
            batch_ids=planned_batch_ids,
            completed_without_tasks=completed_without_tasks,
        )


def dispatch_processing_task() -> None:
    now = datetime.now(ZoneInfo(settings.scheduler_timezone))
    get_processing_runtime().wake(now=now)


def _ths_member_spec(api_name: str, codes: tuple[str, ...]) -> ApiSpec:
    return replace(
        get_api_specs().get(api_name),
        scope_builder=lambda ignored_date: ths_member_scopes(codes),
    )


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
    unknown_stock_recovery = get_processing_repository().reconcile_unknown_stock_failures(now=now)
    if unknown_stock_recovery.requeued_count:
        structlog.get_logger("scheduler").info(
            "unknown_stock_tasks_requeued",
            recovered_count=unknown_stock_recovery.requeued_count,
        )
    if (
        unknown_stock_recovery.master_refresh_required
        and unknown_stock_recovery.latest_failure_at is not None
    ):
        _plan_stage(
            StagePlan(
                batch_type=BatchType.REPAIR,
                business_date=now.date(),
                scheduled_at=unknown_stock_recovery.latest_failure_at,
                api_specs=(STOCK_BASIC_SPEC,),
                finalize=True,
            ),
            now=now,
            stage_name="unknown_stock_master_recovery",
        )


def plan_trade_calendar() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    business_date = now.date()
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
    business_date = now.date()
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
    business_date = now.date()
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
    business_date = now.date()
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
    business_date = now.date()
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
    _plan_daily_stage(
        (*daily_specs, STOCK_BASIC_SPEC),
        finalize=True,
        stage_name="daily_final",
    )


def plan_theme_members() -> None:
    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    business_date = now.date()
    _plan_stage(
        StagePlan(
            batch_type=BatchType.DELAYED,
            business_date=business_date,
            scheduled_at=datetime.combine(business_date, time(hour=20), timezone),
            api_specs=DELAYED_THEME_SPECS,
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
    from app.scheduler.management import execute_scheduled_job

    timezone = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(timezone)
    stages = (
        (time(hour=8, minute=45), "plan-etf-share-size"),
        (time(hour=9, minute=25), "plan-daily-preopen"),
        (time(hour=16, minute=10), "plan-daily-close"),
        (time(hour=17, minute=30), "plan-daily-late"),
        (time(hour=19), "plan-daily-final"),
        (time(hour=20), "plan-theme-members"),
        (time(hour=22, minute=35), "plan-hot-rank"),
    )
    for scheduled_time, job_id in stages:
        if now.time() >= scheduled_time:
            try:
                execute_scheduled_job(job_id, "STARTUP_CATCHUP")
            except CalendarCoverageError:
                structlog.get_logger("scheduler").warning(
                    "daily_stage_waiting_for_calendar",
                    stage=job_id,
                    business_date=now.date().isoformat(),
                )
                break
            except Exception:
                structlog.get_logger("scheduler").exception(
                    "daily_startup_stage_failed",
                    stage=job_id,
                    business_date=now.date().isoformat(),
                )


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
    try:
        result = get_collection_planner().plan(stage, now=now)
    except ClosedBatchPlanMismatchError:
        structlog.get_logger("scheduler").info(
            "collection_stage_already_closed",
            stage=stage_name,
            batch_type=stage.batch_type.value,
            business_date=stage.business_date.isoformat() if stage.business_date else None,
        )
        return
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


def cleanup_scheduled_job_executions() -> None:
    cutoff = datetime.now(ZoneInfo(settings.scheduler_timezone)) - timedelta(
        days=settings.scheduler_execution_retention_days
    )
    with SyncSessionFactory() as session, session.begin():
        result = cast(
            CursorResult[Any],
            session.execute(
                delete(ScheduledJobExecution).where(
                    ScheduledJobExecution.created_at < cutoff,
                    ScheduledJobExecution.status.in_(("SUCCESS", "FAILED")),
                )
            ),
        )
    structlog.get_logger("scheduler").info(
        "scheduled_job_execution_history_cleaned",
        deleted_count=int(result.rowcount or 0),
        retention_days=settings.scheduler_execution_retention_days,
    )


def registered_job_functions() -> dict[str, Callable[[], None]]:
    """Return the canonical scheduler function registry used by cron and manual runs."""
    return {
        "dispatch-collection-tasks": dispatch_collection_tasks,
        "close-collection-batches": close_collection_batches,
        "plan-processing-tasks": plan_processing_tasks,
        "dispatch-processing-task": dispatch_processing_task,
        "reconcile-collection-runtime": reconcile_collection_runtime,
        "reconcile-processing-runtime": reconcile_processing_runtime,
        "plan-trade-calendar": plan_trade_calendar,
        "plan-stock-master": plan_stock_master,
        "plan-etf-master": plan_etf_master,
        "plan-special-master": plan_special_master,
        "plan-concept-board-members": plan_ths_board_members,
        "plan-monthly-index-weights": plan_monthly_index_weights,
        "plan-next-year-trade-calendar": plan_next_year_trade_calendar,
        "plan-daily-preopen": plan_daily_preopen,
        "plan-daily-close": plan_daily_close,
        "plan-daily-late": plan_daily_late,
        "plan-daily-final": plan_daily_final,
        "plan-etf-share-size": plan_etf_share_size,
        "plan-theme-members": plan_theme_members,
        "plan-hot-rank": plan_hot_rank,
        "ensure-future-partitions": ensure_future_partitions,
        "cleanup-scheduled-job-executions": cleanup_scheduled_job_executions,
    }
