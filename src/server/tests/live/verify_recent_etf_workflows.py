from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select

from app.catalog.tushare import (
    ETF_SHARE_SIZE_SPEC,
    FUND_ADJ_SPEC,
    FUND_DAILY_SPEC,
    MASTER_ETF_SPECS,
    TRADE_CAL_SPEC,
)
from app.db.sync_session import SyncSessionFactory
from app.modules.acquisition.models import (
    BatchType,
    CollectionTask,
    RawDataAsset,
)
from app.modules.etfs.models import Etf, EtfDaily, EtfShareSizeDaily
from app.modules.operations.models import ProviderRequestLog
from app.modules.processing.factory import (
    get_dataset_specs,
    get_processing_repository,
    get_processing_runtime,
)
from app.modules.processing.models import DatasetRelease, ProcessingTask, ProcessingTaskStatus
from tests.live.verify_recent_workflows import (
    TIMEZONE,
    LiveWorkflowError,
    OperationsApi,
    _assert_collection_success,
    _assert_no_nonterminal_primary_tasks,
    _batch_ids_from_command,
    _batch_task_rows,
    _close_batches,
    _database_counts,
    _drain_collection,
    _file_uri_path,
    _historical_closed_date,
    _plan_and_drain_processing,
    _plan_stage,
    _run_collection_retries,
    _trading_dates,
    _validate_environment,
    _verify_operations_api,
    _verify_raw_assets,
)

ETF_BACKFILL_APIS = ("fund_daily", "fund_adj", "etf_share_size")


def _progress(message: str) -> None:
    print(message, flush=True)


def _run_processing_retry(api: OperationsApi, business_date: date) -> dict[str, object]:
    result = api.post(
        "/api/v1/operations/commands/repairs",
        {
            "businessDate": business_date.isoformat(),
            "apiNames": ["fund_daily"],
            "reason": "ETF真实全流程验证：加工文件临时不可用后的人工重试",
        },
        f"live-etf-processing-repair-{business_date:%Y%m%d}-v1",
    )
    batch_id = _batch_ids_from_command(result)[0]
    _drain_collection()
    _close_batches()
    _assert_collection_success((batch_id,), "ETF processing retry source")
    plan = get_processing_repository().plan_closed_batches(
        get_dataset_specs().all(),
        now=datetime.now(TIMEZONE),
    )
    if plan.created_task_count < 1:
        raise LiveWorkflowError("ETF processing retry did not create a task")
    with SyncSessionFactory() as session:
        process = session.scalar(
            select(ProcessingTask).where(
                ProcessingTask.source_batch_id == batch_id,
                ProcessingTask.output_dataset == "etf_daily",
            )
        )
        asset = session.scalar(
            select(RawDataAsset)
            .join(CollectionTask, CollectionTask.task_id == RawDataAsset.task_id)
            .where(
                CollectionTask.batch_id == batch_id,
                CollectionTask.api_name == "fund_daily",
            )
        )
        if process is None or asset is None:
            raise LiveWorkflowError("ETF processing retry fixture is incomplete")
        process_id = process.process_id
        storage_uri = asset.storage_uri
    asset_path = _file_uri_path(storage_uri)
    fault_path = asset_path.with_name(f"{asset_path.name}.fault-injected")
    if fault_path.exists():
        raise LiveWorkflowError(f"fault injection path already exists: {fault_path}")
    asset_path.rename(fault_path)
    try:
        transition = get_processing_runtime().dispatch(now=datetime.now(TIMEZONE))
        if transition is None or transition.status != ProcessingTaskStatus.FAILED:
            raise LiveWorkflowError(f"fault-injected ETF processing did not fail: {transition}")
    finally:
        if fault_path.exists():
            fault_path.rename(asset_path)
    retry = api.post(
        f"/api/v1/operations/commands/processing-tasks/{process_id}/retry",
        {"reason": "ETF真实全流程验证：原始资产恢复后重试"},
        f"live-etf-processing-task-retry-{process_id}-v1",
    )
    transition = get_processing_runtime().dispatch(now=datetime.now(TIMEZONE))
    if transition is None or transition.status != ProcessingTaskStatus.SUCCESS:
        raise LiveWorkflowError(f"ETF processing retry did not succeed: {transition}")
    with SyncSessionFactory() as session:
        process = session.get(ProcessingTask, process_id)
        if process is None or process.attempt_count != 2:
            raise LiveWorkflowError("ETF processing retry attempt count is not 2")
    _progress(f"ETF processing retry succeeded: process={process_id}, attempts=2")
    return {
        "sourceBatchId": str(batch_id),
        "processId": str(process_id),
        "attemptCount": 2,
        "commandId": retry["commandId"],
    }


def _verify_business_data(trading_dates: tuple[date, ...]) -> dict[str, object]:
    with SyncSessionFactory() as session:
        etf_count = int(session.scalar(select(func.count()).select_from(Etf)) or 0)
        invalid_exchange = int(
            session.scalar(
                select(func.count()).select_from(Etf).where(Etf.exchange.not_in(("SSE", "SZSE")))
            )
            or 0
        )
        daily: dict[str, dict[str, int]] = {}
        for business_date in trading_dates:
            daily_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(EtfDaily)
                    .where(EtfDaily.trade_date == business_date)
                )
                or 0
            )
            factor_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(EtfDaily)
                    .where(
                        EtfDaily.trade_date == business_date,
                        EtfDaily.adj_factor.is_not(None),
                    )
                )
                or 0
            )
            share_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(EtfShareSizeDaily)
                    .where(EtfShareSizeDaily.trade_date == business_date)
                )
                or 0
            )
            releases = int(
                session.scalar(
                    select(func.count())
                    .select_from(DatasetRelease)
                    .where(
                        DatasetRelease.dataset_name.in_(("etf_daily", "etf_share_size_daily")),
                        DatasetRelease.scope_type == "DATE",
                        DatasetRelease.scope_key == business_date.isoformat(),
                    )
                )
                or 0
            )
            if daily_count < 1_500 or share_count < 1_000 or releases != 2:
                raise LiveWorkflowError(
                    f"ETF business data validation failed for {business_date}: "
                    f"daily={daily_count}, factors={factor_count}, "
                    f"share={share_count}, releases={releases}"
                )
            daily[business_date.isoformat()] = {
                "etfDaily": daily_count,
                "factorPopulated": factor_count,
                "shareSize": share_count,
                "releases": releases,
            }
        minimum_share = session.scalar(select(func.min(EtfShareSizeDaily.total_share)))
        minimum_size = session.scalar(select(func.min(EtfShareSizeDaily.total_size)))
    if etf_count < 1_500 or invalid_exchange:
        raise LiveWorkflowError(
            f"ETF master validation failed: count={etf_count}, invalidExchange={invalid_exchange}"
        )
    if minimum_share is None or minimum_size is None or minimum_share <= 0 or minimum_size <= 0:
        raise LiveWorkflowError("ETF share-size unit validation failed")
    return {
        "etfCount": etf_count,
        "invalidExchangeCount": invalid_exchange,
        "daily": daily,
    }


def _provider_requests_since(started_at: datetime) -> dict[str, dict[str, int | float]]:
    with SyncSessionFactory() as session:
        rows = session.execute(
            select(
                ProviderRequestLog.endpoint,
                func.count(),
                func.count().filter(ProviderRequestLog.status == "SUCCESS"),
                func.coalesce(func.sum(ProviderRequestLog.row_count), 0),
                func.coalesce(func.sum(ProviderRequestLog.rate_limit_wait_ms), 0),
                func.coalesce(func.avg(ProviderRequestLog.duration_ms), 0),
                func.coalesce(func.max(ProviderRequestLog.duration_ms), 0),
            )
            .where(
                ProviderRequestLog.requested_at >= started_at,
                ProviderRequestLog.endpoint.in_(
                    ("etf_basic", "fund_daily", "fund_adj", "etf_share_size")
                ),
            )
            .group_by(ProviderRequestLog.endpoint)
            .order_by(ProviderRequestLog.endpoint)
        ).all()
    result = {
        str(row.endpoint): {
            "requests": int(row[1]),
            "success": int(row[2]),
            "rows": int(row[3]),
            "rateLimitWaitMs": int(row[4]),
            "averageDurationMs": round(float(row[5]), 3),
            "maxDurationMs": int(row[6]),
        }
        for row in rows
    }
    if set(result) != {"etf_basic", "fund_daily", "fund_adj", "etf_share_size"}:
        raise LiveWorkflowError(f"ETF provider request coverage is incomplete: {result}")
    return result


def _scoped_counts(batch_ids: tuple[UUID, ...]) -> dict[str, int]:
    rows = _batch_task_rows(batch_ids)
    with SyncSessionFactory() as session:
        processing_count = int(
            session.scalar(
                select(func.count())
                .select_from(ProcessingTask)
                .where(ProcessingTask.source_batch_id.in_(batch_ids))
            )
            or 0
        )
    return {
        "batches": len(batch_ids),
        "collectionTasks": len(rows),
        "processingTasks": processing_count,
    }


def run(start_date: date, end_date: date, report_path: Path) -> dict[str, object]:
    if (end_date - start_date).days != 13:
        raise LiveWorkflowError("validation range must be exactly 14 calendar days")
    started_at = datetime.now(UTC)
    database_name = _validate_environment()
    api = OperationsApi()
    try:
        month_start = date(end_date.year, end_date.month, 1)
        calendar_batch = _plan_stage(
            batch_type=BatchType.MASTER,
            business_date=month_start,
            scheduled_at=datetime.combine(month_start, day_time(8, 20), TIMEZONE),
            specs=(TRADE_CAL_SPEC,),
            finalize=True,
        )
        _drain_collection()
        _close_batches()
        _assert_collection_success((calendar_batch,), "trade calendar")
        _plan_and_drain_processing((calendar_batch,), "trade calendar")

        master_batch = _plan_stage(
            batch_type=BatchType.MASTER,
            business_date=month_start,
            scheduled_at=datetime.combine(month_start, day_time(8, 35), TIMEZONE),
            specs=MASTER_ETF_SPECS,
            finalize=True,
        )
        _drain_collection()
        _close_batches()
        _assert_collection_success((master_batch,), "ETF master")
        _plan_and_drain_processing((master_batch,), "ETF master")

        trading_dates = _trading_dates(start_date, end_date)
        daily_date = trading_dates[-1]
        daily_scheduled_at = datetime.combine(daily_date, day_time(8, 50), TIMEZONE)
        daily_batch = _plan_stage(
            batch_type=BatchType.DAILY,
            business_date=daily_date,
            scheduled_at=daily_scheduled_at,
            specs=(FUND_DAILY_SPEC,),
            finalize=False,
        )
        _drain_collection()
        planned = _plan_stage(
            batch_type=BatchType.DAILY,
            business_date=daily_date,
            scheduled_at=daily_scheduled_at,
            specs=(FUND_ADJ_SPEC,),
            finalize=True,
        )
        if planned != daily_batch:
            raise LiveWorkflowError("ETF daily stages did not reuse the same batch")
        _drain_collection()
        _close_batches()
        _assert_collection_success((daily_batch,), "ETF daily staged flow")

        delayed_batch = _plan_stage(
            batch_type=BatchType.DELAYED,
            business_date=daily_date,
            scheduled_at=datetime.combine(
                daily_date + timedelta(days=1),
                day_time(8, 45),
                TIMEZONE,
            ),
            specs=(ETF_SHARE_SIZE_SPEC,),
            finalize=True,
        )
        _drain_collection()
        _close_batches()
        _assert_collection_success((delayed_batch,), "ETF delayed share-size flow")

        backfill_end = daily_date - timedelta(days=1)
        backfill = api.post(
            "/api/v1/operations/commands/backfills",
            {
                "startDate": start_date.isoformat(),
                "endDate": backfill_end.isoformat(),
                "apiNames": list(ETF_BACKFILL_APIS),
                "reason": "ETF真实全流程验证：最近两周历史数据回补",
            },
            f"live-etf-backfill-{start_date:%Y%m%d}-{backfill_end:%Y%m%d}-v1",
        )
        backfill_batches = _batch_ids_from_command(backfill)
        expected_backfill_dates = tuple(item for item in trading_dates if item < daily_date)
        if len(backfill_batches) != len(expected_backfill_dates):
            raise LiveWorkflowError(
                f"ETF backfill batch count mismatch: {len(backfill_batches)} "
                f"!= {len(expected_backfill_dates)}"
            )
        _progress(
            f"ETF backfill accepted: dates={len(expected_backfill_dates)}, "
            f"tasks={len(expected_backfill_dates) * 4}"
        )
        _drain_collection()
        _close_batches()
        _assert_collection_success(backfill_batches, "ETF historical backfill")

        primary_batches = (
            calendar_batch,
            master_batch,
            daily_batch,
            delayed_batch,
            *backfill_batches,
        )
        _plan_and_drain_processing(primary_batches, "recent two-week ETF data")
        _assert_no_nonterminal_primary_tasks(primary_batches)
        business = _verify_business_data(trading_dates)

        processing_retry = _run_processing_retry(api, daily_date)
        retries = _run_collection_retries(
            api,
            current_closed_date=end_date,
            historical_closed_date=_historical_closed_date(start_date, end_date),
            api_name="fund_daily",
            idempotency_prefix="live-etf",
        )
        all_asset_batches = (
            *primary_batches,
            UUID(str(processing_retry["sourceBatchId"])),
        )
        raw = _verify_raw_assets(all_asset_batches)
        operations = _verify_operations_api(api)
        report = {
            "generatedAt": datetime.now(UTC).isoformat(),
            "database": database_name,
            "dateRange": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "tradingDates": [item.isoformat() for item in trading_dates],
                "dailyDate": daily_date.isoformat(),
                "backfillDates": [item.isoformat() for item in expected_backfill_dates],
            },
            "batches": {
                "tradeCalendar": str(calendar_batch),
                "master": str(master_batch),
                "daily": str(daily_batch),
                "delayed": str(delayed_batch),
                "backfill": [str(item) for item in backfill_batches],
            },
            "scopeCounts": _scoped_counts(all_asset_batches),
            "rawAssets": raw,
            "businessData": business,
            "providerRequests": _provider_requests_since(started_at),
            "processingRetry": processing_retry,
            "collectionRetries": retries,
            "operationsApi": operations,
            "databaseCounts": _database_counts(),
            "passed": True,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _progress(f"live ETF workflow validation PASS: {report_path}")
        return report
    finally:
        api.close()


def _parse_args() -> argparse.Namespace:
    today = datetime.now(TIMEZONE).date()
    parser = argparse.ArgumentParser(
        description="Validate recent ETF workflows with real Tushare calls"
    )
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        default=today - timedelta(days=13),
    )
    parser.add_argument("--end-date", type=date.fromisoformat, default=today)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(
            os.environ.get(
                "LIVE_ETF_VALIDATION_REPORT",
                "dist/live-validation/recent-etf-workflows.json",
            )
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    run(args.start_date, args.end_date, args.report.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
