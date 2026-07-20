from collections.abc import Awaitable
from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.presentation import TUSHARE_API_PRESENTATION_BY_NAME
from app.catalog.tushare import build_tushare_api_registry
from app.core.config import settings
from app.db.session import get_db
from app.modules.operations.command_service import (
    MAX_BACKFILL_DAYS,
    CommandContext,
    OperationCommandError,
    OperationCommandService,
)
from app.modules.operations.repository import OperationsRepository
from app.modules.operations.schemas import (
    AcquisitionApiOption,
    AcquisitionBatchItem,
    AlertItem,
    CreateBackfillCommand,
    CreateRepairCommand,
    DatasetReleaseCoverageItem,
    DatasetReleaseItem,
    DependencyItem,
    ExecutionStatus,
    ManualCommandOptions,
    OperationCommandResult,
    OperationsOverview,
    PageResult,
    ProcessingQueueItem,
    ProviderMonitoring,
    RunRecordItem,
    ScheduledJobCommand,
    ScheduledJobExecutionItem,
    ScheduledJobItem,
    ScheduledJobStatus,
    TaskCommand,
)
from app.modules.operations.security import AdminPrincipal, require_admin
from app.modules.operations.service import OperationsService

router = APIRouter()
DbSession = Annotated[AsyncSession, Depends(get_db)]
Admin = Annotated[AdminPrincipal, Depends(require_admin)]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]


def _service(db: AsyncSession) -> OperationsService:
    return OperationsService(OperationsRepository(db))


@router.get("/overview", response_model=OperationsOverview)
async def overview(db: DbSession) -> OperationsOverview:
    return await _service(db).overview()


@router.get(
    "/acquisition-batches",
    response_model=PageResult[AcquisitionBatchItem],
)
async def acquisition_batches(
    db: DbSession,
    status: ExecutionStatus | None = None,
    data_cycle: Annotated[date | None, Query(alias="dataCycle")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=200)] = 20,
) -> PageResult[AcquisitionBatchItem]:
    return await _service(db).acquisition_batches(
        status=status,
        business_date=data_cycle,
        page=page,
        page_size=page_size,
    )


@router.get("/processing-queue", response_model=PageResult[ProcessingQueueItem])
async def processing_queue(
    db: DbSession,
    status: ExecutionStatus | None = None,
    dataset_name: Annotated[str | None, Query(alias="datasetName", max_length=64)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=200)] = 20,
) -> PageResult[ProcessingQueueItem]:
    return await _service(db).processing_queue(
        status=status,
        dataset_name=dataset_name,
        page=page,
        page_size=page_size,
    )


@router.get("/dependencies", response_model=PageResult[DependencyItem])
async def dependencies(
    db: DbSession,
    readiness: Literal["attention", "waiting", "blocked", "ready", "all"] = "attention",
    query: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=200)] = 20,
) -> PageResult[DependencyItem]:
    return await _service(db).dependencies(
        readiness=readiness,
        query=query,
        page=page,
        page_size=page_size,
    )


@router.get("/releases", response_model=PageResult[DatasetReleaseItem])
async def releases(
    db: DbSession,
    dataset_name: Annotated[str | None, Query(alias="datasetName", max_length=64)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=200)] = 20,
) -> PageResult[DatasetReleaseItem]:
    return await _service(db).releases(
        dataset_name=dataset_name,
        page=page,
        page_size=page_size,
    )


@router.get("/release-coverage", response_model=list[DatasetReleaseCoverageItem])
async def release_coverage(
    db: DbSession,
    start_date: Annotated[date | None, Query(alias="startDate")] = None,
    end_date: Annotated[date | None, Query(alias="endDate")] = None,
    day_count: Annotated[int | None, Query(alias="dayCount", ge=1, le=30)] = None,
) -> list[DatasetReleaseCoverageItem]:
    today = datetime.now(ZoneInfo(settings.scheduler_timezone)).date()
    resolved_end = end_date or today
    if resolved_end > today:
        raise HTTPException(status_code=422, detail="结束日期不能晚于今天")
    if start_date is not None and start_date > resolved_end:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
    if start_date is not None and resolved_end - start_date > timedelta(days=3660):
        raise HTTPException(status_code=422, detail="单次检查范围不能超过 10 年")
    if start_date is None and day_count is None:
        start_date = resolved_end - timedelta(days=29)
    return await _service(db).release_coverage(
        start_date=start_date,
        end_date=resolved_end,
        day_count=day_count if start_date is None else None,
    )


@router.get("/providers/tushare", response_model=ProviderMonitoring)
async def provider_monitoring(db: DbSession) -> ProviderMonitoring:
    return await _service(db).provider_monitoring()


@router.get("/runs", response_model=PageResult[RunRecordItem])
async def run_records(
    db: DbSession,
    run_type: Annotated[
        Literal["acquisition", "processing"] | None,
        Query(alias="runType"),
    ] = None,
    status: ExecutionStatus | None = None,
    batch_id: Annotated[UUID | None, Query(alias="batchId")] = None,
    unresolved_only: Annotated[bool, Query(alias="unresolvedOnly")] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=200)] = 20,
) -> PageResult[RunRecordItem]:
    return await _service(db).run_records(
        run_type=run_type,
        status=status,
        batch_id=batch_id,
        unresolved_only=unresolved_only,
        page=page,
        page_size=page_size,
    )


@router.get("/scheduled-jobs", response_model=list[ScheduledJobItem])
async def scheduled_jobs(db: DbSession) -> list[ScheduledJobItem]:
    return await _service(db).scheduled_jobs()


@router.get(
    "/scheduled-job-executions",
    response_model=PageResult[ScheduledJobExecutionItem],
)
async def scheduled_job_executions(
    db: DbSession,
    job_id: Annotated[str | None, Query(alias="jobId", max_length=96)] = None,
    status: ScheduledJobStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=200)] = 20,
) -> PageResult[ScheduledJobExecutionItem]:
    return await _service(db).scheduled_job_executions(
        job_id=job_id,
        status=status,
        page=page,
        page_size=page_size,
    )


@router.get("/alerts", response_model=PageResult[AlertItem])
async def alerts(
    db: DbSession,
    source: Annotated[
        Literal["acquisition", "processing", "scheduler", "storage"] | None,
        Query(),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=200)] = 20,
) -> PageResult[AlertItem]:
    return await _service(db).alerts(
        source=source,
        page=page,
        page_size=page_size,
    )


@router.get("/command-options", response_model=ManualCommandOptions)
async def command_options() -> ManualCommandOptions:
    return ManualCommandOptions(
        generated_at=datetime.now().astimezone(),
        acquisition_apis=[
            AcquisitionApiOption(
                api_name=spec.api_name,
                display_name=TUSHARE_API_PRESENTATION_BY_NAME[spec.api_name].display_name,
                description=TUSHARE_API_PRESENTATION_BY_NAME[spec.api_name].description,
                schedule_group=spec.schedule_group.value,
            )
            for spec in build_tushare_api_registry().all()
        ],
        max_backfill_days=MAX_BACKFILL_DAYS,
    )


@router.post("/commands/backfills", response_model=OperationCommandResult, status_code=202)
async def create_backfill(
    payload: CreateBackfillCommand,
    request: Request,
    db: DbSession,
    admin: Admin,
    idempotency_key: IdempotencyKey,
) -> OperationCommandResult:
    service = OperationCommandService(db, build_tushare_api_registry())
    return await _run_command(
        service.create_backfill(
            start_date=payload.start_date,
            end_date=payload.end_date,
            api_names=payload.api_names,
            reason=payload.reason,
            context=_context(request, admin, idempotency_key),
        ),
        db=db,
        request=request,
        action="CREATE_BACKFILL",
    )


@router.post("/commands/repairs", response_model=OperationCommandResult, status_code=202)
async def create_repair(
    payload: CreateRepairCommand,
    request: Request,
    db: DbSession,
    admin: Admin,
    idempotency_key: IdempotencyKey,
) -> OperationCommandResult:
    service = OperationCommandService(db, build_tushare_api_registry())
    return await _run_command(
        service.create_repair(
            business_date=payload.business_date,
            api_names=payload.api_names,
            reason=payload.reason,
            context=_context(request, admin, idempotency_key),
        ),
        db=db,
        request=request,
        action="CREATE_REPAIR",
    )


@router.post(
    "/commands/scheduled-jobs/{job_id}/{action}",
    response_model=OperationCommandResult,
    status_code=202,
)
async def manage_scheduled_job(
    job_id: str,
    action: Literal["enable", "disable", "run"],
    payload: ScheduledJobCommand,
    request: Request,
    db: DbSession,
    admin: Admin,
    idempotency_key: IdempotencyKey,
) -> OperationCommandResult:
    service = OperationCommandService(db, build_tushare_api_registry())
    context = _context(request, admin, idempotency_key)
    command = (
        service.request_scheduled_job_run(job_id, reason=payload.reason, context=context)
        if action == "run"
        else service.set_scheduled_job_enabled(
            job_id,
            enabled=action == "enable",
            reason=payload.reason,
            context=context,
        )
    )
    return await _run_command(
        command,
        db=db,
        request=request,
        action=f"{action.upper()}_SCHEDULED_JOB",
    )


@router.post(
    "/commands/collection-tasks/{task_id}/retry",
    response_model=OperationCommandResult,
    status_code=202,
)
async def retry_collection_task(
    task_id: UUID,
    payload: TaskCommand,
    request: Request,
    db: DbSession,
    admin: Admin,
    idempotency_key: IdempotencyKey,
) -> OperationCommandResult:
    return await _run_command(
        OperationCommandService(db, build_tushare_api_registry()).retry_collection_task(
            task_id,
            reason=payload.reason,
            context=_context(request, admin, idempotency_key),
        ),
        db=db,
        request=request,
        action="RETRY_COLLECTION_TASK",
    )


@router.post(
    "/commands/collection-tasks/{task_id}/{transition}",
    response_model=OperationCommandResult,
    status_code=202,
)
async def transition_collection_task(
    task_id: UUID,
    transition: Literal["skip", "cancel"],
    payload: TaskCommand,
    request: Request,
    db: DbSession,
    admin: Admin,
    idempotency_key: IdempotencyKey,
) -> OperationCommandResult:
    action = f"{transition.upper()}_COLLECTION_TASK"
    return await _run_command(
        OperationCommandService(db, build_tushare_api_registry()).transition_collection_task(
            task_id,
            action=action,
            reason=payload.reason,
            context=_context(request, admin, idempotency_key),
        ),
        db=db,
        request=request,
        action=action,
    )


@router.post(
    "/commands/acquisition-batches/{batch_id}/retry-failed-tasks",
    response_model=OperationCommandResult,
    status_code=202,
)
async def retry_failed_collection_tasks(
    batch_id: UUID,
    payload: TaskCommand,
    request: Request,
    db: DbSession,
    admin: Admin,
    idempotency_key: IdempotencyKey,
) -> OperationCommandResult:
    return await _run_command(
        OperationCommandService(
            db, build_tushare_api_registry()
        ).retry_failed_collection_tasks(
            batch_id,
            reason=payload.reason,
            context=_context(request, admin, idempotency_key),
        ),
        db=db,
        request=request,
        action="RETRY_FAILED_COLLECTION_TASKS",
    )


@router.post(
    "/commands/processing-tasks/{process_id}/retry",
    response_model=OperationCommandResult,
    status_code=202,
)
async def retry_processing_task(
    process_id: UUID,
    payload: TaskCommand,
    request: Request,
    db: DbSession,
    admin: Admin,
    idempotency_key: IdempotencyKey,
) -> OperationCommandResult:
    return await _run_command(
        OperationCommandService(db, build_tushare_api_registry()).retry_processing_task(
            process_id,
            reason=payload.reason,
            context=_context(request, admin, idempotency_key),
        ),
        db=db,
        request=request,
        action="RETRY_PROCESSING_TASK",
    )


@router.post(
    "/commands/processing-tasks/retry-all-failed",
    response_model=OperationCommandResult,
    status_code=202,
)
async def retry_all_failed_processing_tasks(
    payload: TaskCommand,
    request: Request,
    db: DbSession,
    admin: Admin,
    idempotency_key: IdempotencyKey,
) -> OperationCommandResult:
    return await _run_command(
        OperationCommandService(
            db, build_tushare_api_registry()
        ).retry_all_failed_processing_tasks(
            reason=payload.reason,
            context=_context(request, admin, idempotency_key),
        ),
        db=db,
        request=request,
        action="RETRY_ALL_FAILED_PROCESSING_TASKS",
    )


@router.post(
    "/commands/processing-tasks/{process_id}/{transition}",
    response_model=OperationCommandResult,
    status_code=202,
)
async def transition_processing_task(
    process_id: UUID,
    transition: Literal["skip", "cancel"],
    payload: TaskCommand,
    request: Request,
    db: DbSession,
    admin: Admin,
    idempotency_key: IdempotencyKey,
) -> OperationCommandResult:
    action = f"{transition.upper()}_PROCESSING_TASK"
    return await _run_command(
        OperationCommandService(db, build_tushare_api_registry()).transition_processing_task(
            process_id,
            action=action,
            reason=payload.reason,
            context=_context(request, admin, idempotency_key),
        ),
        db=db,
        request=request,
        action=action,
    )


@router.post(
    "/commands/acquisition-batches/{batch_id}/cancel",
    response_model=OperationCommandResult,
    status_code=202,
)
async def cancel_acquisition_batch(
    batch_id: UUID,
    payload: TaskCommand,
    request: Request,
    db: DbSession,
    admin: Admin,
    idempotency_key: IdempotencyKey,
) -> OperationCommandResult:
    return await _run_command(
        OperationCommandService(db, build_tushare_api_registry()).cancel_batch(
            batch_id,
            reason=payload.reason,
            context=_context(request, admin, idempotency_key),
        ),
        db=db,
        request=request,
        action="CANCEL_COLLECTION_BATCH",
    )


def _context(
    request: Request,
    admin: AdminPrincipal,
    idempotency_key: str,
) -> CommandContext:
    return CommandContext(
        idempotency_key=idempotency_key,
        actor=admin.name,
        request_id=getattr(request.state, "request_id", "unknown"),
        client_ip=request.client.host if request.client else None,
    )


async def _run_command(
    command: Awaitable[OperationCommandResult],
    *,
    db: AsyncSession,
    request: Request,
    action: str,
) -> OperationCommandResult:
    try:
        return await command
    except OperationCommandError as exc:
        await db.rollback()
        structlog.get_logger("operations.audit").warning(
            "admin_command_rejected",
            action=action,
            request_id=getattr(request.state, "request_id", "unknown"),
            client_ip=request.client.host if request.client else None,
            reason=str(exc),
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
