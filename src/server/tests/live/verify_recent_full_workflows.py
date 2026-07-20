from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text

from app.catalog.tushare import (
    DAILY_CLOSE_SPECS,
    DAILY_FINAL_SPECS,
    DAILY_LATE_SPECS,
    DAILY_PREOPEN_SPECS,
    DELAYED_ETF_SPECS,
    HOT_SPECS,
    MASTER_ETF_SPECS,
    MASTER_SPECIAL_SPECS,
    MASTER_STOCK_SPECS,
    TRADE_CAL_SPEC,
)
from app.db.sync_session import SyncSessionFactory
from app.modules.acquisition.models import BatchType, CollectionTask, RawDataAsset
from app.modules.etfs.models import Etf, EtfDaily, EtfShareSizeDaily
from app.modules.indices.models import (
    IndexDailyBasic,
    MarketIndex,
    MarketIndexDaily,
    MarketIndexWeight,
)
from app.modules.operations.models import ProviderRequestLog
from app.modules.processing.factory import (
    get_dataset_specs,
    get_processing_repository,
    get_processing_runtime,
)
from app.modules.processing.models import DatasetRelease, ProcessingTask, ProcessingTaskStatus
from app.modules.stocks.models import (
    Stock,
    StockCompany,
    StockDaily,
    StockMoneyflowDaily,
    StockSuspendDaily,
    StockTechnicalDaily,
    ThsBoardMoneyflowDaily,
    TradeCalendar,
)
from app.modules.topics.models import (
    ConceptBoard,
    ConceptBoardDaily,
    ConceptBoardMember,
    MarketThemeDaily,
    MarketThemeMemberDaily,
    StockHotRankDaily,
    StockLimitEventDaily,
    StockLimitStepDaily,
    StockTopInstDaily,
    StockTopListDaily,
    ThemeIndex,
    ThemeIndexDaily,
    ThemeIndexMember,
)
from tests.live.verify_recent_workflows import (
    TIMEZONE,
    LiveWorkflowError,
    OperationsApi,
    _assert_collection_success,
    _assert_no_nonterminal_primary_tasks,
    _batch_ids_from_command,
    _close_batches,
    _database_counts,
    _drain_collection,
    _file_uri_path,
    _first_task,
    _historical_closed_date,
    _plan_and_drain_processing,
    _plan_stage,
    _trading_dates,
    _validate_environment,
    _verify_operations_api,
    _verify_raw_assets,
)

DAILY_SPECS = (
    *DAILY_PREOPEN_SPECS,
    *DAILY_CLOSE_SPECS,
    *DAILY_LATE_SPECS,
    *DAILY_FINAL_SPECS,
)
BASE_BACKFILL_APIS = tuple(spec.api_name for spec in (*DAILY_SPECS, *DELAYED_ETF_SPECS, *HOT_SPECS))
DATE_RELEASES = {
    "stock_daily.core",
    "stock_daily.limit",
    "stock_technical_daily",
    "stock_moneyflow_daily",
    "stock_suspend_daily",
    "etf_daily",
    "etf_share_size_daily",
    "concept_board_daily",
    "theme_index_daily",
    "stock_hot_rank_daily",
    "market_theme_daily",
    "market_theme_member_daily",
    "stock_top_list_daily",
    "stock_top_inst_daily",
    "stock_limit_event_daily",
    "stock_limit_step_daily",
    "ths_board_moneyflow_daily",
    "market_index_daily",
    "index_daily_basic",
}
TABLE_MODELS = {
    "trade_calendar": TradeCalendar,
    "stock": Stock,
    "stock_company": StockCompany,
    "stock_daily": StockDaily,
    "stock_technical_daily": StockTechnicalDaily,
    "stock_moneyflow_daily": StockMoneyflowDaily,
    "ths_board_moneyflow_daily": ThsBoardMoneyflowDaily,
    "stock_suspend_daily": StockSuspendDaily,
    "concept_board": ConceptBoard,
    "concept_board_daily": ConceptBoardDaily,
    "concept_board_member": ConceptBoardMember,
    "theme_index": ThemeIndex,
    "theme_index_daily": ThemeIndexDaily,
    "theme_index_member": ThemeIndexMember,
    "stock_hot_rank_daily": StockHotRankDaily,
    "market_theme_daily": MarketThemeDaily,
    "market_theme_member_daily": MarketThemeMemberDaily,
    "stock_top_list_daily": StockTopListDaily,
    "stock_top_inst_daily": StockTopInstDaily,
    "stock_limit_event_daily": StockLimitEventDaily,
    "stock_limit_step_daily": StockLimitStepDaily,
    "market_index": MarketIndex,
    "market_index_daily": MarketIndexDaily,
    "index_daily_basic": IndexDailyBasic,
    "market_index_weight": MarketIndexWeight,
    "etf": Etf,
    "etf_daily": EtfDaily,
    "etf_share_size_daily": EtfShareSizeDaily,
}


def _progress(message: str) -> None:
    print(message, flush=True)


def _collect_master(
    month_start: date,
) -> tuple[UUID, UUID, UUID, UUID]:
    stages = (
        ("trade calendar", day_time(8, 20), (TRADE_CAL_SPEC,)),
        ("stock master", day_time(8, 30), MASTER_STOCK_SPECS),
        ("ETF master", day_time(8, 35), MASTER_ETF_SPECS),
        ("special master", day_time(8, 40), MASTER_SPECIAL_SPECS),
    )
    batch_ids: list[UUID] = []
    for label, scheduled_time, specs in stages:
        batch_id = _plan_stage(
            batch_type=BatchType.MASTER,
            business_date=month_start,
            scheduled_at=datetime.combine(month_start, scheduled_time, TIMEZONE),
            specs=specs,
            finalize=True,
        )
        _drain_collection()
        _close_batches()
        _assert_collection_success((batch_id,), label)
        _plan_and_drain_processing((batch_id,), label)
        batch_ids.append(batch_id)
    return tuple(batch_ids)  # type: ignore[return-value]


def _collect_master_entities(
    api: OperationsApi,
    month_start: date,
) -> tuple[UUID, UUID]:
    concept_result = api.post(
        "/api/v1/operations/commands/repairs",
        {
            "businessDate": month_start.isoformat(),
            "apiNames": ["ths_member"],
            "reason": "最近一周全流程验证：概念成分完整快照",
        },
        f"live-full-concept-member-{month_start:%Y%m}-v1",
    )
    concept_batch = _batch_ids_from_command(concept_result)[0]
    _drain_collection()
    _close_batches()
    _assert_collection_success((concept_batch,), "concept members")
    _plan_and_drain_processing((concept_batch,), "concept members")

    target_month = (month_start - timedelta(days=1)).replace(day=1)
    weight_result = api.post(
        "/api/v1/operations/commands/repairs",
        {
            "businessDate": target_month.isoformat(),
            "apiNames": ["index_weight"],
            "reason": "最近一周全流程验证：上一完整月份指数权重",
        },
        f"live-full-index-weight-{target_month:%Y%m}-v1",
    )
    weight_batch = _batch_ids_from_command(weight_result)[0]
    _drain_collection()
    _close_batches()
    _assert_collection_success((weight_batch,), "index weights")
    _plan_and_drain_processing((weight_batch,), "index weights")
    return concept_batch, weight_batch


def _collect_current_daily(business_date: date) -> tuple[UUID, UUID, UUID]:
    daily_scheduled_at = datetime.combine(business_date, day_time(8, 45), TIMEZONE)
    daily_batch: UUID | None = None
    for specs, finalize in (
        (DAILY_PREOPEN_SPECS, False),
        (DAILY_CLOSE_SPECS, False),
        (DAILY_LATE_SPECS, False),
        (DAILY_FINAL_SPECS, True),
    ):
        planned = _plan_stage(
            batch_type=BatchType.DAILY,
            business_date=business_date,
            scheduled_at=daily_scheduled_at,
            specs=specs,
            finalize=finalize,
        )
        if daily_batch is not None and planned != daily_batch:
            raise LiveWorkflowError("daily stages did not reuse the same batch")
        daily_batch = planned
        _drain_collection()
    if daily_batch is None:
        raise LiveWorkflowError("daily batch was not created")

    delayed_batch = _plan_stage(
        batch_type=BatchType.DELAYED,
        business_date=business_date,
        scheduled_at=datetime.combine(business_date + timedelta(days=1), day_time(8, 45), TIMEZONE),
        specs=DELAYED_ETF_SPECS,
        finalize=True,
    )
    hot_batch = _plan_stage(
        batch_type=BatchType.HOT,
        business_date=business_date,
        scheduled_at=datetime.combine(business_date, day_time(22, 35), TIMEZONE),
        specs=HOT_SPECS,
        finalize=True,
    )
    _drain_collection()
    _close_batches()
    batch_ids = (daily_batch, delayed_batch, hot_batch)
    _assert_collection_success(batch_ids, "scheduled daily flow")
    return batch_ids


def _collect_historical_base(
    api: OperationsApi,
    start_date: date,
    end_date: date,
) -> tuple[UUID, ...]:
    result = api.post(
        "/api/v1/operations/commands/backfills",
        {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "apiNames": list(BASE_BACKFILL_APIS),
            "reason": "最近一周全流程验证：历史基础数据回补",
        },
        f"live-full-base-backfill-{start_date:%Y%m%d}-{end_date:%Y%m%d}-v1",
    )
    batch_ids = _batch_ids_from_command(result)
    _progress(f"historical base backfill accepted: batches={len(batch_ids)}")
    _drain_collection()
    _close_batches()
    _assert_collection_success(batch_ids, "historical base backfill")
    return batch_ids


def _collect_theme_members(
    api: OperationsApi,
    start_date: date,
    end_date: date,
) -> tuple[UUID, ...]:
    result = api.post(
        "/api/v1/operations/commands/backfills",
        {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "apiNames": ["dc_concept_cons"],
            "reason": "最近一周全流程验证：题材主表发布后的成员拆分回补",
        },
        f"live-full-theme-member-{start_date:%Y%m%d}-{end_date:%Y%m%d}-v1",
    )
    batch_ids = _batch_ids_from_command(result)
    _progress(f"theme-member backfill accepted: batches={len(batch_ids)}")
    _drain_collection()
    _close_batches()
    _assert_collection_success(batch_ids, "theme-member backfill")
    return batch_ids


def _run_processing_retry(api: OperationsApi, business_date: date) -> dict[str, object]:
    result = api.post(
        "/api/v1/operations/commands/repairs",
        {
            "businessDate": business_date.isoformat(),
            "apiNames": ["top_list"],
            "reason": "最近一周全流程验证：龙虎榜加工故障重试",
        },
        f"live-full-processing-repair-{business_date:%Y%m%d}-v1",
    )
    batch_id = _batch_ids_from_command(result)[0]
    _drain_collection()
    _close_batches()
    _assert_collection_success((batch_id,), "processing retry source")
    get_processing_repository().plan_closed_batches(
        get_dataset_specs().all(), now=datetime.now(TIMEZONE)
    )
    with SyncSessionFactory() as session:
        process = session.scalar(
            select(ProcessingTask).where(
                ProcessingTask.source_batch_id == batch_id,
                ProcessingTask.output_dataset == "stock_top_list_daily",
            )
        )
        asset = session.scalar(
            select(RawDataAsset)
            .join(CollectionTask, CollectionTask.task_id == RawDataAsset.task_id)
            .where(CollectionTask.batch_id == batch_id)
        )
        if process is None or asset is None:
            raise LiveWorkflowError("processing retry fixture is incomplete")
        process_id = process.process_id
        process_status = process.status
        process_attempt_count = process.attempt_count
        asset_path = _file_uri_path(asset.storage_uri)
    if process_status == ProcessingTaskStatus.SUCCESS.value and process_attempt_count >= 2:
        retry = api.post(
            f"/api/v1/operations/commands/processing-tasks/{process_id}/retry",
            {"reason": "原始资产恢复，验证加工任务人工重试"},
            f"live-full-processing-retry-{process_id}-v1",
        )
        return {
            "batchId": str(batch_id),
            "processId": str(process_id),
            "attemptCount": process_attempt_count,
            "commandId": retry["commandId"],
        }
    fault_path = asset_path.with_name(f"{asset_path.name}.fault-injected")
    asset_path.rename(fault_path)
    try:
        transition = get_processing_runtime().dispatch(now=datetime.now(TIMEZONE))
        if transition is None or transition.status != ProcessingTaskStatus.FAILED:
            raise LiveWorkflowError(f"fault-injected processing did not fail: {transition}")
    finally:
        fault_path.rename(asset_path)
    retry = api.post(
        f"/api/v1/operations/commands/processing-tasks/{process_id}/retry",
        {"reason": "原始资产恢复，验证加工任务人工重试"},
        f"live-full-processing-retry-{process_id}-v1",
    )
    transition = get_processing_runtime().dispatch(now=datetime.now(TIMEZONE))
    if transition is None or transition.status != ProcessingTaskStatus.SUCCESS:
        raise LiveWorkflowError(f"processing retry did not succeed: {transition}")
    return {
        "batchId": str(batch_id),
        "processId": str(process_id),
        "attemptCount": 2,
        "commandId": retry["commandId"],
    }


def _run_collection_retry(api: OperationsApi, closed_date: date) -> dict[str, object]:
    failed = api.post(
        "/api/v1/operations/commands/repairs",
        {
            "businessDate": closed_date.isoformat(),
            "apiNames": ["daily"],
            "reason": "最近一周全流程验证：休市日空结果失败",
        },
        f"live-full-collection-failure-{closed_date:%Y%m%d}-v1",
    )
    source_batch = _batch_ids_from_command(failed)[0]
    source_task = _drain_collection_to_terminal(source_batch)
    if source_task.status != "FAILED":
        raise LiveWorkflowError(f"closed-day collection did not fail: {source_task.status}")
    _close_batches()
    retried = api.post(
        f"/api/v1/operations/commands/collection-tasks/{source_task.task_id}/retry",
        {"reason": "验证终态采集任务人工重试会创建新修复批次"},
        f"live-full-collection-retry-{source_task.task_id}-v1",
    )
    retry_batch = _batch_ids_from_command(retried)[0]
    retry_task = _drain_collection_to_terminal(retry_batch)
    if retry_task.status != "FAILED" or retry_task.task_id == source_task.task_id:
        raise LiveWorkflowError("manual collection retry did not create a new failed attempt")
    _close_batches()
    synthetic_batches = (source_batch, retry_batch)
    get_processing_repository().plan_closed_batches(
        get_dataset_specs().all(), now=datetime.now(TIMEZONE)
    )
    with SyncSessionFactory() as session:
        synthetic_processes = tuple(
            session.execute(
                select(ProcessingTask.process_id, ProcessingTask.status).where(
                    ProcessingTask.source_batch_id.in_(synthetic_batches)
                )
            )
        )
    for process_id, status in synthetic_processes:
        if status != ProcessingTaskStatus.BLOCKED.value:
            continue
        api.post(
            f"/api/v1/operations/commands/processing-tasks/{process_id}/cancel",
            {"reason": "休市日采集失败验证产生的预期阻塞任务，验收后取消"},
            f"live-cancel-synthetic-blocked-{process_id}-v1",
        )
    with SyncSessionFactory() as session:
        remaining = int(
            session.scalar(
                select(func.count())
                .select_from(ProcessingTask)
                .where(
                    ProcessingTask.source_batch_id.in_(synthetic_batches),
                    ProcessingTask.status != ProcessingTaskStatus.CANCELLED.value,
                )
            )
            or 0
        )
    if remaining:
        raise LiveWorkflowError("synthetic collection failures left non-cancelled processing tasks")
    return {
        "sourceBatchId": str(source_batch),
        "sourceTaskId": str(source_task.task_id),
        "retryBatchId": str(retry_batch),
        "retryTaskId": str(retry_task.task_id),
        "commandId": retried["commandId"],
        "cancelledProcessingTaskIds": [
            str(process_id) for process_id, _status in synthetic_processes
        ],
    }


def _drain_collection_to_terminal(batch_id: UUID) -> CollectionTask:
    for _attempt in range(10):
        _drain_collection()
        task = _first_task(batch_id)
        if task.status != "RETRY_WAIT":
            return task
        with SyncSessionFactory.begin() as session:
            persisted = session.get(CollectionTask, task.task_id)
            if persisted is None or persisted.next_retry_at is None:
                raise LiveWorkflowError("retry-wait task has no retry deadline")
            persisted.next_retry_at = datetime.now(TIMEZONE) - timedelta(seconds=1)
    raise LiveWorkflowError(f"collection task did not reach a terminal state: {batch_id}")


def _verify_business_data(trading_dates: tuple[date, ...]) -> dict[str, object]:
    with SyncSessionFactory() as session:
        table_counts = {
            name: int(session.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in TABLE_MODELS.items()
        }
        empty_tables = [name for name, count in table_counts.items() if count == 0]
        if empty_tables:
            raise LiveWorkflowError(f"business tables are empty: {empty_tables}")

        daily_counts: dict[str, dict[str, int]] = {}
        for business_date in trading_dates:
            releases = set(
                session.scalars(
                    select(DatasetRelease.dataset_name).where(
                        DatasetRelease.scope_type == "DATE",
                        DatasetRelease.scope_key == business_date.isoformat(),
                    )
                )
            )
            if releases != DATE_RELEASES:
                raise LiveWorkflowError(
                    f"release coverage mismatch for {business_date}: "
                    f"missing={sorted(DATE_RELEASES - releases)}, "
                    f"unexpected={sorted(releases - DATE_RELEASES)}"
                )
            counts = {
                "stockDaily": _count_date(session, StockDaily, business_date),
                "etfDaily": _count_date(session, EtfDaily, business_date),
                "etfShareSize": _count_date(session, EtfShareSizeDaily, business_date),
                "conceptDaily": _count_date(session, ConceptBoardDaily, business_date),
                "themeIndexDaily": _count_date(session, ThemeIndexDaily, business_date),
                "hotRank": _count_date(session, StockHotRankDaily, business_date),
                "themeDaily": _count_date(session, MarketThemeDaily, business_date),
                "themeMembers": _count_date(session, MarketThemeMemberDaily, business_date),
                "topList": _count_date(session, StockTopListDaily, business_date),
                "topInst": _count_date(session, StockTopInstDaily, business_date),
                "limitEvents": _count_date(session, StockLimitEventDaily, business_date),
                "limitSteps": _count_date(session, StockLimitStepDaily, business_date),
                "boardMoneyflow": _count_date(session, ThsBoardMoneyflowDaily, business_date),
                "indexDaily": _count_date(session, MarketIndexDaily, business_date),
                "indexDailyBasic": _count_date(session, IndexDailyBasic, business_date),
                "releases": len(releases),
            }
            if any(count == 0 for name, count in counts.items() if name != "releases"):
                raise LiveWorkflowError(
                    f"one or more daily datasets are empty for {business_date}: {counts}"
                )
            daily_counts[business_date.isoformat()] = counts

        partition_rows = {
            row.table_name: int(row.row_count)
            for row in session.execute(
                text(
                    """
                    SELECT tableoid::regclass::text AS table_name, count(*) AS row_count
                    FROM market_theme_member_daily
                    GROUP BY tableoid
                    """
                )
            )
        }
    return {
        "tables": table_counts,
        "daily": daily_counts,
        "themeMemberPartitions": partition_rows,
    }


def _count_date(session: Any, model: Any, business_date: date) -> int:
    table = model.__table__
    result = session.scalar(
        select(func.count()).select_from(model).where(table.c.trade_date == business_date)
    )
    return int(result or 0)


def _provider_stats(batch_ids: tuple[UUID, ...]) -> dict[str, dict[str, int | float]]:
    with SyncSessionFactory() as session:
        rows = session.execute(
            select(
                ProviderRequestLog.endpoint,
                func.count(),
                func.count().filter(ProviderRequestLog.status == "SUCCESS"),
                func.coalesce(func.sum(ProviderRequestLog.row_count), 0),
                func.coalesce(func.sum(ProviderRequestLog.rate_limit_wait_ms), 0),
                func.coalesce(func.avg(ProviderRequestLog.duration_ms), 0),
            )
            .join(CollectionTask, CollectionTask.task_id == ProviderRequestLog.task_id)
            .where(CollectionTask.batch_id.in_(batch_ids))
            .group_by(ProviderRequestLog.endpoint)
            .order_by(ProviderRequestLog.endpoint)
        ).all()
    return {
        str(row.endpoint): {
            "requests": int(row[1]),
            "success": int(row[2]),
            "rows": int(row[3]),
            "rateLimitWaitMs": int(row[4]),
            "averageDurationMs": round(float(row[5]), 3),
        }
        for row in rows
    }


def run(start_date: date, end_date: date, report_path: Path) -> dict[str, object]:
    if (end_date - start_date).days != 6:
        raise LiveWorkflowError("validation range must be exactly seven calendar days")
    database_name = _validate_environment()
    api = OperationsApi()
    try:
        month_start = date(end_date.year, end_date.month, 1)
        master_batches = _collect_master(month_start)
        entity_batches = _collect_master_entities(api, month_start)
        trading_dates = _trading_dates(start_date, end_date)
        daily_date = trading_dates[-1]

        current_batches = _collect_current_daily(daily_date)
        historical_dates = tuple(item for item in trading_dates if item < daily_date)
        if not historical_dates:
            raise LiveWorkflowError("validation range has no historical trading dates")
        historical_batches = _collect_historical_base(
            api, historical_dates[0], historical_dates[-1]
        )
        base_batches = (*historical_batches, *current_batches)
        _plan_and_drain_processing(base_batches, "recent-week base datasets")
        _assert_no_nonterminal_primary_tasks(base_batches)

        theme_batches = _collect_theme_members(api, trading_dates[0], trading_dates[-1])
        _plan_and_drain_processing(theme_batches, "recent-week theme members")
        _assert_no_nonterminal_primary_tasks(theme_batches)

        business = _verify_business_data(trading_dates)
        processing_retry = _run_processing_retry(api, daily_date)
        collection_retry = _run_collection_retry(
            api, _historical_closed_date(start_date, end_date)
        )
        primary_batches = (
            *master_batches,
            *entity_batches,
            *base_batches,
            *theme_batches,
            UUID(str(processing_retry["batchId"])),
        )
        raw_assets = _verify_raw_assets(primary_batches)
        provider_stats = _provider_stats(primary_batches)
        operations = _verify_operations_api(api)
        report = {
            "generatedAt": datetime.now(UTC).isoformat(),
            "database": database_name,
            "dateRange": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "tradingDates": [value.isoformat() for value in trading_dates],
                "scheduledDailyDate": daily_date.isoformat(),
                "historicalBackfillDates": [value.isoformat() for value in historical_dates],
            },
            "batches": {
                "master": [str(value) for value in master_batches],
                "masterEntities": [str(value) for value in entity_batches],
                "base": [str(value) for value in base_batches],
                "themeMembers": [str(value) for value in theme_batches],
            },
            "businessData": business,
            "rawAssets": raw_assets,
            "providerStats": provider_stats,
            "processingRetry": processing_retry,
            "collectionRetry": collection_retry,
            "operationsApi": operations,
            "databaseCounts": _database_counts(),
            "passed": True,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _progress(f"full workflow report written: {report_path}")
        return report
    finally:
        api.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.start, arguments.end, arguments.report)


if __name__ == "__main__":
    main()
