from __future__ import annotations

import argparse
import json
import time
from collections import Counter, deque
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.catalog import ScheduleGroup
from app.catalog.tushare import build_tushare_api_registry
from app.core.config import settings
from app.db.sync_session import SyncSessionFactory
from app.modules.acquisition.domain import TERMINAL_TASK_STATUSES
from app.modules.acquisition.factory import (
    get_acquisition_repository,
    get_acquisition_runtime,
    shutdown_acquisition_runtime,
)
from app.modules.acquisition.models import (
    BatchStatus,
    CollectionBatch,
    CollectionTask,
    CollectionTaskStatus,
    RawDataAsset,
)
from app.modules.operations.models import DeferredCollectionStage, ProviderRequestLog
from app.modules.processing.factory import (
    get_dataset_specs,
    get_processing_repository,
    get_processing_runtime,
    shutdown_processing_runtime,
)
from app.modules.processing.models import (
    DatasetRelease,
    ProcessingDependency,
    ProcessingTask,
    ProcessingTaskStatus,
)
from app.modules.processing.repository import PROCESSING_TERMINAL_STATUSES
from app.scheduler.jobs import plan_deferred_collection_stages
from tests.live.verify_recent_full_workflows import DATE_RELEASES
from tests.live.verify_recent_workflows import (
    TIMEZONE,
    LiveWorkflowError,
    OperationsApi,
    _assert_processing_concurrency,
    _batch_ids_from_command,
    _validate_environment,
    _verify_operations_api,
    _verify_raw_assets,
)

SUCCESSFUL_COLLECTION_STATUSES = {
    CollectionTaskStatus.SUCCESS.value,
    CollectionTaskStatus.EMPTY_VALID.value,
}


def _progress(message: str) -> None:
    print(message, flush=True)


def _backfill_api_names() -> tuple[str, ...]:
    allowed = {ScheduleGroup.DAILY, ScheduleGroup.DELAYED, ScheduleGroup.HOT}
    return tuple(
        spec.api_name
        for spec in build_tushare_api_registry().all()
        if spec.schedule_group in allowed
    )


def _command_batch_ids(command_id: UUID, initial_batch_ids: Sequence[UUID]) -> tuple[UUID, ...]:
    with SyncSessionFactory() as session:
        deferred = tuple(
            session.scalars(
                select(DeferredCollectionStage.batch_id)
                .where(
                    DeferredCollectionStage.command_id == command_id,
                    DeferredCollectionStage.batch_id.is_not(None),
                )
                .order_by(DeferredCollectionStage.business_date)
            )
        )
    return tuple(dict.fromkeys((*initial_batch_ids, *(item for item in deferred if item))))


def _state(command_id: UUID, batch_ids: Sequence[UUID]) -> dict[str, Any]:
    with SyncSessionFactory() as session:
        batch_statuses = Counter(
            session.scalars(
                select(CollectionBatch.status).where(CollectionBatch.batch_id.in_(batch_ids))
            )
        )
        collection_statuses = Counter(
            session.scalars(
                select(CollectionTask.status).where(CollectionTask.batch_id.in_(batch_ids))
            )
        )
        processing_statuses = Counter(
            session.scalars(
                select(ProcessingTask.status).where(
                    ProcessingTask.source_batch_id.in_(batch_ids)
                )
            )
        )
        deferred_statuses = Counter(
            session.scalars(
                select(DeferredCollectionStage.status).where(
                    DeferredCollectionStage.command_id == command_id
                )
            )
        )
    return {
        "batches": dict(batch_statuses),
        "collection": dict(collection_statuses),
        "processing": dict(processing_statuses),
        "deferred": dict(deferred_statuses),
    }


def _terminal_success(command_id: UUID, batch_ids: Sequence[UUID]) -> bool:
    state = _state(command_id, batch_ids)
    if state["deferred"].get("PENDING", 0):
        return False
    if sum(state["batches"].values()) != len(batch_ids):
        return False
    if state["batches"].get(BatchStatus.CLOSED.value, 0) != len(batch_ids):
        return False
    if any(
        status not in TERMINAL_TASK_STATUSES
        for status, count in state["collection"].items()
        if count
    ):
        return False
    if not state["processing"]:
        return False
    return not any(
        status not in PROCESSING_TERMINAL_STATUSES
        for status, count in state["processing"].items()
        if count
    )


def _raise_for_failures(batch_ids: Sequence[UUID]) -> None:
    with SyncSessionFactory() as session:
        collection_failures = session.execute(
            select(
                CollectionTask.task_id,
                CollectionTask.api_name,
                CollectionTask.status,
                CollectionTask.error_code,
                CollectionTask.error_message,
            ).where(
                CollectionTask.batch_id.in_(batch_ids),
                CollectionTask.status.not_in(SUCCESSFUL_COLLECTION_STATUSES),
            )
        ).all()
        processing_failures = session.execute(
            select(
                ProcessingTask.process_id,
                ProcessingTask.output_dataset,
                ProcessingTask.status,
                ProcessingTask.error_message,
            ).where(
                ProcessingTask.source_batch_id.in_(batch_ids),
                ProcessingTask.status != ProcessingTaskStatus.SUCCESS.value,
            )
        ).all()
    if collection_failures:
        raise LiveWorkflowError(
            "collection failures: "
            + repr(
                [
                    {
                        "task": str(row.task_id),
                        "api": row.api_name,
                        "status": row.status,
                        "code": row.error_code,
                        "error": str(row.error_message or "")[:300],
                    }
                    for row in collection_failures
                ]
            )
        )
    if processing_failures:
        raise LiveWorkflowError(
            "processing failures: "
            + repr(
                [
                    {
                        "process": str(row.process_id),
                        "dataset": row.output_dataset,
                        "status": row.status,
                        "error": str(row.error_message or "")[:300],
                    }
                    for row in processing_failures
                ]
            )
        )


def _release_rows(
    trading_dates: Sequence[date],
    process_ids: Sequence[UUID],
) -> dict[str, dict[str, int]]:
    with SyncSessionFactory() as session:
        rows = session.execute(
            select(
                DatasetRelease.business_date,
                DatasetRelease.dataset_name,
                DatasetRelease.row_count,
            ).where(
                DatasetRelease.business_date.in_(trading_dates),
                DatasetRelease.process_id.in_(process_ids),
            )
        ).all()
    by_date: dict[str, dict[str, int]] = {}
    for business_date, dataset_name, row_count in rows:
        if business_date is None:
            continue
        by_date.setdefault(business_date.isoformat(), {})[str(dataset_name)] = int(row_count)
    for business_date in trading_dates:
        datasets = set(by_date.get(business_date.isoformat(), {}))
        if datasets != DATE_RELEASES:
            raise LiveWorkflowError(
                f"release coverage mismatch for {business_date}: "
                f"missing={sorted(DATE_RELEASES - datasets)}, "
                f"unexpected={sorted(datasets - DATE_RELEASES)}"
            )
    return by_date


def _theme_member_inputs(batch_ids: Sequence[UUID]) -> dict[str, dict[str, Any]]:
    with SyncSessionFactory() as session:
        rows = session.execute(
            select(
                ProcessingTask.business_date,
                ProcessingTask.source_batch_id,
                ProcessingTask.rows_read,
                ProcessingTask.rows_rejected,
                ProcessingTask.warning_message,
                ProcessingDependency.dependency_scope_key,
                RawDataAsset.row_count,
                CollectionTask.batch_id,
            )
            .join(
                ProcessingDependency,
                ProcessingDependency.process_id == ProcessingTask.process_id,
            )
            .join(RawDataAsset, RawDataAsset.asset_id == ProcessingDependency.resolved_asset_id)
            .join(CollectionTask, CollectionTask.task_id == RawDataAsset.task_id)
            .where(
                ProcessingTask.source_batch_id.in_(batch_ids),
                ProcessingTask.output_dataset == "market_theme_member_daily",
                ProcessingDependency.dependency_name == "dc_concept_cons",
            )
            .order_by(ProcessingTask.business_date, ProcessingDependency.dependency_scope_key)
        ).all()
    by_date: dict[str, list[Any]] = {}
    for row in rows:
        if row.business_date is not None:
            by_date.setdefault(row.business_date.isoformat(), []).append(row)
    result: dict[str, dict[str, Any]] = {}
    for business_date, dependencies in by_date.items():
        if len(dependencies) != 1:
            raise LiveWorkflowError(
                f"{business_date} theme member processing resolved {len(dependencies)} raw assets"
            )
        dependency = dependencies[0]
        if dependency.batch_id != dependency.source_batch_id:
            raise LiveWorkflowError(
                f"{business_date} theme member processing reused an older raw asset"
            )
        if int(dependency.rows_read or -1) != int(dependency.row_count):
            raise LiveWorkflowError(
                f"{business_date} theme member rows_read does not match its raw asset"
            )
        result[business_date] = {
            "scopeKey": dependency.dependency_scope_key,
            "rawRows": int(dependency.row_count),
            "rowsRead": int(dependency.rows_read),
            "rowsRejected": int(dependency.rows_rejected or 0),
            "warning": dependency.warning_message,
        }
    return result


def _peak_in_window(values: Sequence[datetime], seconds: float = 60.0) -> int:
    ordered = sorted(values)
    active: deque[datetime] = deque()
    peak = 0
    for value in ordered:
        while active and (value - active[0]).total_seconds() >= seconds:
            active.popleft()
        active.append(value)
        peak = max(peak, len(active))
    return peak


def _execution_metrics(
    tasks: Sequence[CollectionTask] | Sequence[ProcessingTask],
    *,
    old_workers_per_tick: int,
) -> dict[str, int | float | None]:
    completed = [
        item for item in tasks if item.started_at is not None and item.finished_at is not None
    ]
    if not completed:
        return {
            "taskCount": 0,
            "wallSeconds": None,
            "dispatchSpanSeconds": None,
            "taskStartsPerMinute": 0,
            "oldFiveSecondGateSeconds": 0,
            "dispatchSpeedup": None,
        }
    starts = [item.started_at for item in completed if item.started_at is not None]
    finishes = [item.finished_at for item in completed if item.finished_at is not None]
    wall_seconds = (max(finishes) - min(starts)).total_seconds()
    dispatch_span_seconds = (max(starts) - min(starts)).total_seconds()
    old_gate_seconds = ((len(completed) - 1) // old_workers_per_tick) * 5
    return {
        "taskCount": len(completed),
        "wallSeconds": round(wall_seconds, 3),
        "dispatchSpanSeconds": round(dispatch_span_seconds, 3),
        "taskStartsPerMinute": _peak_in_window(starts),
        "oldFiveSecondGateSeconds": old_gate_seconds,
        "dispatchSpeedup": (
            round(old_gate_seconds / dispatch_span_seconds, 2)
            if dispatch_span_seconds > 0
            else None
        ),
    }


def _metrics(
    initial_batch_ids: Sequence[UUID],
    deferred_batch_ids: Sequence[UUID],
) -> dict[str, Any]:
    all_batch_ids = (*initial_batch_ids, *deferred_batch_ids)
    with SyncSessionFactory() as session:
        initial_collection = list(
            session.scalars(
                select(CollectionTask).where(CollectionTask.batch_id.in_(initial_batch_ids))
            )
        )
        deferred_collection = list(
            session.scalars(
                select(CollectionTask).where(CollectionTask.batch_id.in_(deferred_batch_ids))
            )
        )
        processing = list(
            session.scalars(
                select(ProcessingTask).where(
                    ProcessingTask.source_batch_id.in_(all_batch_ids)
                )
            )
        )
        provider_times = tuple(
            session.scalars(
                select(ProviderRequestLog.requested_at)
                .join(CollectionTask, CollectionTask.task_id == ProviderRequestLog.task_id)
                .where(CollectionTask.batch_id.in_(all_batch_ids))
            )
        )
        provider_request_count = len(provider_times)
        provider_success_count = int(
            session.scalar(
                select(func.count())
                .select_from(ProviderRequestLog)
                .join(CollectionTask, CollectionTask.task_id == ProviderRequestLog.task_id)
                .where(
                    CollectionTask.batch_id.in_(all_batch_ids),
                    ProviderRequestLog.status == "SUCCESS",
                )
            )
            or 0
        )
        asset_count = int(
            session.scalar(
                select(func.count())
                .select_from(RawDataAsset)
                .join(CollectionTask, CollectionTask.task_id == RawDataAsset.task_id)
                .where(CollectionTask.batch_id.in_(all_batch_ids))
            )
            or 0
        )
        endpoint_rows = session.execute(
            select(
                ProviderRequestLog.endpoint,
                ProviderRequestLog.status,
                func.count(),
            )
            .join(CollectionTask, CollectionTask.task_id == ProviderRequestLog.task_id)
            .where(CollectionTask.batch_id.in_(all_batch_ids))
            .group_by(ProviderRequestLog.endpoint, ProviderRequestLog.status)
        ).all()

    endpoint_requests: dict[str, dict[str, int]] = {}
    for endpoint, status, count in endpoint_rows:
        endpoint_requests.setdefault(str(endpoint), {})[str(status)] = int(count)

    processing_max_concurrency = _assert_processing_concurrency(
        processing, "June historical backfill"
    )
    processing_metrics = _execution_metrics(processing, old_workers_per_tick=1)
    collection_metrics = {
        "initial": _execution_metrics(
            initial_collection,
            old_workers_per_tick=settings.collection_max_workers,
        ),
        "deferred": _execution_metrics(
            deferred_collection,
            old_workers_per_tick=settings.collection_max_workers,
        ),
    }
    return {
        "collection": collection_metrics,
        "processing": processing_metrics,
        "processingMaxConcurrency": processing_max_concurrency,
        "providerRequests": provider_request_count,
        "providerSuccess": provider_success_count,
        "providerPeakRequests60s": _peak_in_window(provider_times),
        "providerRequestsByEndpoint": endpoint_requests,
        "rawAssetCount": asset_count,
    }


def _assert_efficiency(metrics: dict[str, Any]) -> None:
    initial = metrics["collection"]["initial"]
    processing = metrics["processing"]
    if int(initial["taskStartsPerMinute"]) < 96:
        raise LiveWorkflowError(
            f"collection throughput did not materially exceed old 48/min: {initial}"
        )
    if int(processing["taskStartsPerMinute"]) < 24:
        raise LiveWorkflowError(
            f"processing throughput did not materially exceed old 12/min: {processing}"
        )
    if int(metrics["processingMaxConcurrency"]) < 2:
        raise LiveWorkflowError(f"processing concurrency was not observed: {metrics}")
    if int(metrics["providerPeakRequests60s"]) > settings.tushare_request_budget_per_minute:
        raise LiveWorkflowError(f"local request budget exceeded: {metrics}")
    pagination = metrics["themeMemberPagination"]
    if float(pagination["requestReductionFactor"]) < 10:
        raise LiveWorkflowError(f"theme member pagination improvement is insufficient: {metrics}")


def run(start_date: date, end_date: date, report_path: Path, run_id: str) -> dict[str, Any]:
    database_name = _validate_environment()
    api_names = _backfill_api_names()
    api = OperationsApi()
    started_at = datetime.now(UTC)
    clock_started = time.monotonic()
    try:
        command = api.post(
            "/api/v1/operations/commands/backfills",
            {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "apiNames": list(api_names),
                "reason": "真实并发回归：验证历史回填、限流和加工连续补位",
            },
            f"live-concurrency-backfill-{start_date:%Y%m%d}-{end_date:%Y%m%d}-{run_id}",
        )
        command_id = UUID(str(command["commandId"]))
        initial_batch_ids = _batch_ids_from_command(command)
        result = command.get("result", {})
        deferred_count = int(result.get("deferredStageCount", 0))
        trading_date_count = int(result.get("tradingDateCount", 0))
        _progress(
            f"backfill accepted: tradingDates={trading_date_count}, "
            f"batches={len(initial_batch_ids)}, apis={len(api_names)}, "
            f"deferredStages={deferred_count}"
        )

        acquisition_runtime = get_acquisition_runtime()
        processing_runtime = get_processing_runtime()
        next_dispatch = time.monotonic()
        next_poll = next_dispatch
        next_progress = next_dispatch
        deadline = next_dispatch + 3600
        batch_ids = initial_batch_ids

        while True:
            now_clock = time.monotonic()
            if now_clock >= deadline:
                raise LiveWorkflowError(f"backfill timed out: {_state(command_id, batch_ids)}")
            now = datetime.now(TIMEZONE)
            if now_clock >= next_dispatch:
                acquisition_runtime.dispatch(now=now)
                processing_runtime.wake(now=now)
                next_dispatch = now_clock + 5
            if now_clock >= next_poll:
                get_acquisition_repository().close_ready_batches(now=now)
                batch_ids = _command_batch_ids(command_id, initial_batch_ids)
                get_processing_repository().plan_closed_batches(
                    get_dataset_specs().all(),
                    now=now,
                    source_batch_ids=batch_ids,
                )
                plan_deferred_collection_stages()
                batch_ids = _command_batch_ids(command_id, initial_batch_ids)
                next_poll = now_clock + settings.scheduler_poll_seconds
            if now_clock >= next_progress:
                elapsed = round(now_clock - clock_started, 1)
                _progress(f"elapsed={elapsed}s state={_state(command_id, batch_ids)}")
                next_progress = now_clock + 10
            if (
                _terminal_success(command_id, batch_ids)
                and acquisition_runtime.inflight_count() == 0
                and processing_runtime.inflight_count() == 0
            ):
                break
            time.sleep(0.2)

        _raise_for_failures(batch_ids)
        with SyncSessionFactory() as session:
            trading_dates = tuple(
                session.scalars(
                    select(CollectionBatch.business_date)
                    .where(CollectionBatch.batch_id.in_(initial_batch_ids))
                    .order_by(CollectionBatch.business_date)
                )
            )
            process_ids = tuple(
                session.scalars(
                    select(ProcessingTask.process_id).where(
                        ProcessingTask.source_batch_id.in_(batch_ids)
                    )
                )
            )
        valid_trading_dates = tuple(item for item in trading_dates if item is not None)
        releases = _release_rows(valid_trading_dates, process_ids)
        theme_member_inputs = _theme_member_inputs(batch_ids)
        if set(theme_member_inputs) != {item.isoformat() for item in valid_trading_dates}:
            raise LiveWorkflowError("theme member dependency coverage does not match trading dates")
        deferred_batch_ids = tuple(item for item in batch_ids if item not in initial_batch_ids)
        raw_assets = _verify_raw_assets(batch_ids)
        metrics = _metrics(initial_batch_ids, deferred_batch_ids)
        legacy_theme_requests = sum(
            int(datasets["market_theme_daily"]) for datasets in releases.values()
        )
        new_theme_requests = sum(
            metrics["providerRequestsByEndpoint"].get("dc_concept_cons", {}).values()
        )
        if new_theme_requests == 0:
            raise LiveWorkflowError("dc_concept_cons produced no provider request observations")
        metrics["themeMemberPagination"] = {
            "legacyPerThemeProjectedRequests": legacy_theme_requests,
            "datePaginationRequests": new_theme_requests,
            "requestReductionFactor": round(legacy_theme_requests / new_theme_requests, 2),
        }
        _assert_efficiency(metrics)
        operations = _verify_operations_api(api)
        report = {
            "generatedAt": datetime.now(UTC).isoformat(),
            "database": database_name,
            "dateRange": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "tradingDates": [item.isoformat() for item in valid_trading_dates],
            },
            "commandId": str(command_id),
            "apiNames": list(api_names),
            "batches": {
                "initial": [str(item) for item in initial_batch_ids],
                "deferred": [str(item) for item in deferred_batch_ids],
            },
            "elapsedSeconds": round(time.monotonic() - clock_started, 3),
            "runtimeConfig": {
                "collectionWorkers": settings.collection_max_workers,
                "processingWorkers": settings.processing_max_workers,
                "schedulerPollSeconds": settings.scheduler_poll_seconds,
                "tushareRequestBudgetPerMinute": settings.tushare_request_budget_per_minute,
            },
            "startedAt": started_at.isoformat(),
            "state": _state(command_id, batch_ids),
            "metrics": metrics,
            "rawAssets": raw_assets,
            "releases": releases,
            "themeMemberInputs": theme_member_inputs,
            "operationsApi": operations,
            "passed": True,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _progress(f"backfill concurrency validation PASS: {report_path}")
        return report
    finally:
        shutdown_processing_runtime()
        shutdown_acquisition_runtime()
        api.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    arguments = parser.parse_args()
    run(arguments.start, arguments.end, arguments.report, arguments.run_id)


if __name__ == "__main__":
    main()
