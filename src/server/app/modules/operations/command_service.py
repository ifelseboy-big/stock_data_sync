import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import ApiSpec, ScheduleGroup, SpecRegistry
from app.catalog.specs import ParameterValue, RequestScope
from app.catalog.tushare import theme_member_scopes, ths_member_scopes
from app.modules.acquisition.models import (
    BatchStatus,
    BatchType,
    CollectionBatch,
    CollectionTask,
    CollectionTaskStatus,
)
from app.modules.operations.models import (
    OperationCommand,
    ScheduledJobControl,
    ScheduledJobExecution,
)
from app.modules.operations.schemas import OperationCommandResult
from app.modules.partitions.service import ensure_partitions_for_range
from app.modules.processing.models import (
    DependencyStatus,
    ProcessingDependency,
    ProcessingTask,
    ProcessingTaskStatus,
)
from app.modules.stocks.models import TradeCalendar
from app.modules.topics.models import ConceptBoard, MarketThemeDaily, ThemeIndex
from app.scheduler.catalog import SCHEDULED_JOB_BY_ID, ScheduledJobDefinition

COLLECTION_RETRYABLE = frozenset(
    {
        CollectionTaskStatus.FAILED.value,
        CollectionTaskStatus.SKIPPED.value,
        CollectionTaskStatus.CANCELLED.value,
    }
)
COLLECTION_MUTABLE = frozenset(
    {CollectionTaskStatus.PENDING.value, CollectionTaskStatus.RETRY_WAIT.value}
)
PROCESSING_RETRYABLE = frozenset(
    {
        ProcessingTaskStatus.FAILED.value,
        ProcessingTaskStatus.SKIPPED.value,
        ProcessingTaskStatus.CANCELLED.value,
        ProcessingTaskStatus.BLOCKED.value,
        ProcessingTaskStatus.RETRY_WAIT.value,
    }
)
PROCESSING_MUTABLE = frozenset(
    {
        ProcessingTaskStatus.WAITING_DEPENDENCY.value,
        ProcessingTaskStatus.QUEUED.value,
        ProcessingTaskStatus.RETRY_WAIT.value,
        ProcessingTaskStatus.BLOCKED.value,
    }
)
MAX_BACKFILL_DAYS = 3660


class OperationCommandError(Exception):
    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


class OperationCommandService:
    def __init__(
        self,
        session: AsyncSession,
        api_specs: SpecRegistry[ApiSpec],
    ) -> None:
        self._session = session
        self._api_specs = api_specs

    async def create_backfill(
        self,
        *,
        start_date: date,
        end_date: date,
        api_names: Sequence[str],
        reason: str,
        context: "CommandContext",
    ) -> OperationCommandResult:
        payload: dict[str, object] = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "api_names": list(api_names),
            "reason": reason,
        }

        async def apply(now: datetime) -> dict[str, object]:
            if end_date < start_date:
                raise OperationCommandError("结束日期不能早于开始日期", status_code=422)
            if (end_date - start_date).days + 1 > MAX_BACKFILL_DAYS:
                raise OperationCommandError(
                    f"单次回填范围不能超过 {MAX_BACKFILL_DAYS} 天",
                    status_code=422,
                )
            specs = self._resolve_specs(api_names, daily_only=True)
            trading_dates = await self._trading_dates(start_date, end_date)
            connection = await self._session.connection()
            partition_names = await connection.run_sync(
                lambda sync_connection: ensure_partitions_for_range(
                    sync_connection,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
            batch_ids = [
                str(
                    await self._create_batch(
                        batch_type=BatchType.BACKFILL,
                        business_date=business_date,
                        specs=specs,
                        now=now,
                    )
                )
                for business_date in trading_dates
            ]
            return {
                "batchIds": batch_ids,
                "batchCount": len(batch_ids),
                "tradingDateCount": len(trading_dates),
                "partitionCount": len(partition_names),
            }

        return await self._execute(
            action="CREATE_BACKFILL",
            target_type="collection_batch",
            target_id=None,
            reason=reason,
            payload=payload,
            context=context,
            apply=apply,
        )

    async def create_repair(
        self,
        *,
        business_date: date | None,
        api_names: Sequence[str],
        reason: str,
        context: "CommandContext",
    ) -> OperationCommandResult:
        payload: dict[str, object] = {
            "business_date": business_date.isoformat() if business_date else None,
            "api_names": list(api_names),
            "reason": reason,
        }

        async def apply(now: datetime) -> dict[str, object]:
            specs = self._resolve_specs(api_names, daily_only=False)
            batch_id = await self._create_batch(
                batch_type=BatchType.REPAIR,
                business_date=business_date,
                specs=specs,
                now=now,
            )
            return {"batchId": str(batch_id)}

        return await self._execute(
            action="CREATE_REPAIR",
            target_type="collection_batch",
            target_id=None,
            reason=reason,
            payload=payload,
            context=context,
            apply=apply,
        )

    async def set_scheduled_job_enabled(
        self,
        job_id: str,
        *,
        enabled: bool,
        reason: str,
        context: "CommandContext",
    ) -> OperationCommandResult:
        self._scheduled_job(job_id)

        async def apply(now: datetime) -> dict[str, object]:
            await self._session.execute(
                insert(ScheduledJobControl)
                .values(
                    job_id=job_id,
                    enabled=enabled,
                    updated_at=now,
                    updated_by=context.actor,
                )
                .on_conflict_do_update(
                    index_elements=(ScheduledJobControl.job_id,),
                    set_={
                        "enabled": enabled,
                        "updated_at": now,
                        "updated_by": context.actor,
                    },
                )
            )
            return {"jobId": job_id, "enabled": enabled}

        return await self._execute(
            action="ENABLE_SCHEDULED_JOB" if enabled else "DISABLE_SCHEDULED_JOB",
            target_type="scheduled_job",
            target_id=job_id,
            reason=reason,
            payload={"job_id": job_id, "enabled": enabled, "reason": reason},
            context=context,
            apply=apply,
        )

    async def request_scheduled_job_run(
        self,
        job_id: str,
        *,
        reason: str,
        context: "CommandContext",
    ) -> OperationCommandResult:
        definition = self._scheduled_job(job_id)
        if not definition.manual_allowed:
            raise OperationCommandError("该任务不允许人工执行", status_code=422)

        async def apply(now: datetime) -> dict[str, object]:
            active_count = await self._session.scalar(
                select(func.count())
                .select_from(ScheduledJobExecution)
                .where(
                    ScheduledJobExecution.job_id == job_id,
                    ScheduledJobExecution.status.in_(("PENDING", "RUNNING")),
                )
            )
            if int(active_count or 0):
                raise OperationCommandError("该定时任务已有待执行或运行中的人工请求")
            execution_id = uuid4()
            self._session.add(
                ScheduledJobExecution(
                    execution_id=execution_id,
                    job_id=job_id,
                    trigger_type="MANUAL",
                    status="PENDING",
                    requested_by=context.actor,
                    reason=reason,
                    scheduled_at=now,
                    created_at=now,
                )
            )
            await self._session.flush()
            return {"jobId": job_id, "executionId": str(execution_id), "status": "PENDING"}

        return await self._execute(
            action="RUN_SCHEDULED_JOB",
            target_type="scheduled_job",
            target_id=job_id,
            reason=reason,
            payload={"job_id": job_id, "reason": reason},
            context=context,
            apply=apply,
        )

    async def retry_collection_task(
        self,
        task_id: UUID,
        *,
        reason: str,
        context: "CommandContext",
    ) -> OperationCommandResult:
        async def apply(now: datetime) -> dict[str, object]:
            task = await self._session.scalar(
                select(CollectionTask).where(CollectionTask.task_id == task_id).with_for_update()
            )
            if task is None:
                raise OperationCommandError("采集任务不存在", status_code=404)
            if task.status not in COLLECTION_RETRYABLE:
                raise OperationCommandError(f"状态 {task.status} 不允许人工重试")
            source_batch = await self._session.get(CollectionBatch, task.batch_id)
            if source_batch is None:
                raise OperationCommandError("采集任务所属批次不存在", status_code=404)
            batch_id = await self._create_batch_from_task(task, source_batch, now=now)
            return {"batchId": str(batch_id), "sourceTaskId": str(task_id)}

        return await self._execute_task_command(
            action="RETRY_COLLECTION_TASK",
            target_type="collection_task",
            target_id=task_id,
            reason=reason,
            context=context,
            apply=apply,
        )

    async def transition_collection_task(
        self,
        task_id: UUID,
        *,
        action: str,
        reason: str,
        context: "CommandContext",
    ) -> OperationCommandResult:
        target_status = {
            "SKIP_COLLECTION_TASK": CollectionTaskStatus.SKIPPED,
            "CANCEL_COLLECTION_TASK": CollectionTaskStatus.CANCELLED,
        }[action]

        async def apply(now: datetime) -> dict[str, object]:
            task = await self._session.scalar(
                select(CollectionTask).where(CollectionTask.task_id == task_id).with_for_update()
            )
            if task is None:
                raise OperationCommandError("采集任务不存在", status_code=404)
            if task.status not in COLLECTION_MUTABLE:
                raise OperationCommandError(f"状态 {task.status} 不允许{_verb(action)}")
            task.status = target_status.value
            task.next_retry_at = None
            task.finished_at = now
            task.error_code = f"MANUAL_{target_status.value}"
            task.error_message = reason
            return {"taskId": str(task_id), "status": target_status.value}

        return await self._execute_task_command(
            action=action,
            target_type="collection_task",
            target_id=task_id,
            reason=reason,
            context=context,
            apply=apply,
        )

    async def retry_processing_task(
        self,
        process_id: UUID,
        *,
        reason: str,
        context: "CommandContext",
    ) -> OperationCommandResult:
        async def apply(now: datetime) -> dict[str, object]:
            task = await self._session.scalar(
                select(ProcessingTask)
                .where(ProcessingTask.process_id == process_id)
                .with_for_update()
            )
            if task is None:
                raise OperationCommandError("加工任务不存在", status_code=404)
            if task.status not in PROCESSING_RETRYABLE:
                raise OperationCommandError(f"状态 {task.status} 不允许人工重试")
            unavailable = await self._session.scalar(
                select(func.count())
                .select_from(ProcessingDependency)
                .where(
                    ProcessingDependency.process_id == process_id,
                    ProcessingDependency.status != DependencyStatus.READY.value,
                )
            )
            if unavailable:
                raise OperationCommandError("加工任务仍有未就绪依赖，不能进入执行队列")
            task.status = ProcessingTaskStatus.QUEUED.value
            task.max_attempts = max(task.max_attempts, task.attempt_count + 1)
            task.next_retry_at = None
            task.queued_at = now
            task.started_at = None
            task.finished_at = None
            task.error_message = None
            return {"processId": str(process_id), "status": task.status}

        return await self._execute_task_command(
            action="RETRY_PROCESSING_TASK",
            target_type="processing_task",
            target_id=process_id,
            reason=reason,
            context=context,
            apply=apply,
        )

    async def transition_processing_task(
        self,
        process_id: UUID,
        *,
        action: str,
        reason: str,
        context: "CommandContext",
    ) -> OperationCommandResult:
        target_status = {
            "SKIP_PROCESSING_TASK": ProcessingTaskStatus.SKIPPED,
            "CANCEL_PROCESSING_TASK": ProcessingTaskStatus.CANCELLED,
        }[action]

        async def apply(now: datetime) -> dict[str, object]:
            task = await self._session.scalar(
                select(ProcessingTask)
                .where(ProcessingTask.process_id == process_id)
                .with_for_update()
            )
            if task is None:
                raise OperationCommandError("加工任务不存在", status_code=404)
            if task.status not in PROCESSING_MUTABLE:
                raise OperationCommandError(f"状态 {task.status} 不允许{_verb(action)}")
            task.status = target_status.value
            task.next_retry_at = None
            task.finished_at = now
            task.error_message = reason
            await self._block_processing_downstream(process_id, reason)
            return {"processId": str(process_id), "status": target_status.value}

        return await self._execute_task_command(
            action=action,
            target_type="processing_task",
            target_id=process_id,
            reason=reason,
            context=context,
            apply=apply,
        )

    async def cancel_batch(
        self,
        batch_id: UUID,
        *,
        reason: str,
        context: "CommandContext",
    ) -> OperationCommandResult:
        async def apply(now: datetime) -> dict[str, object]:
            batch = await self._session.scalar(
                select(CollectionBatch)
                .where(CollectionBatch.batch_id == batch_id)
                .with_for_update()
            )
            if batch is None:
                raise OperationCommandError("采集批次不存在", status_code=404)
            if batch.status not in {BatchStatus.PENDING.value, BatchStatus.RUNNING.value}:
                raise OperationCommandError(f"状态 {batch.status} 不允许取消")
            running = await self._session.scalar(
                select(func.count())
                .select_from(CollectionTask)
                .where(
                    CollectionTask.batch_id == batch_id,
                    CollectionTask.status == CollectionTaskStatus.RUNNING.value,
                )
            )
            if running:
                raise OperationCommandError("批次仍有正在执行的采集任务，不能取消")
            cancellable = (
                CollectionTaskStatus.PENDING.value,
                CollectionTaskStatus.RETRY_WAIT.value,
            )
            cancelled_task_count = await self._session.scalar(
                select(func.count())
                .select_from(CollectionTask)
                .where(
                    CollectionTask.batch_id == batch_id,
                    CollectionTask.status.in_(cancellable),
                )
            )
            await self._session.execute(
                update(CollectionTask)
                .where(
                    CollectionTask.batch_id == batch_id,
                    CollectionTask.status.in_(cancellable),
                )
                .values(
                    status=CollectionTaskStatus.CANCELLED.value,
                    next_retry_at=None,
                    finished_at=now,
                    error_code="MANUAL_CANCELLED",
                    error_message=reason,
                )
            )
            batch.status = BatchStatus.CANCELLED.value
            batch.closed_at = now
            return {
                "batchId": str(batch_id),
                "status": batch.status,
                "cancelledTaskCount": int(cancelled_task_count or 0),
            }

        return await self._execute_task_command(
            action="CANCEL_COLLECTION_BATCH",
            target_type="collection_batch",
            target_id=batch_id,
            reason=reason,
            context=context,
            apply=apply,
        )

    async def _execute_task_command(
        self,
        *,
        action: str,
        target_type: str,
        target_id: UUID,
        reason: str,
        context: "CommandContext",
        apply: Callable[[datetime], Awaitable[dict[str, object]]],
    ) -> OperationCommandResult:
        return await self._execute(
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            reason=reason,
            payload={"target_id": str(target_id), "reason": reason},
            context=context,
            apply=apply,
        )

    async def _execute(
        self,
        *,
        action: str,
        target_type: str,
        target_id: str | None,
        reason: str,
        payload: dict[str, object],
        context: "CommandContext",
        apply: Callable[[datetime], Awaitable[dict[str, object]]],
    ) -> OperationCommandResult:
        now = datetime.now(UTC)
        request_hash = _request_hash(action, target_type, target_id, payload)
        command_id = uuid4()
        inserted = await self._session.scalar(
            insert(OperationCommand)
            .values(
                command_id=command_id,
                idempotency_key=context.idempotency_key,
                request_hash=request_hash,
                action=action,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
                actor=context.actor,
                request_id=context.request_id,
                client_ip=context.client_ip,
                status="PENDING",
                request_payload=payload,
                result={},
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=(OperationCommand.idempotency_key,))
            .returning(OperationCommand.command_id)
        )
        if inserted is None:
            existing = await self._session.scalar(
                select(OperationCommand).where(
                    OperationCommand.idempotency_key == context.idempotency_key
                )
            )
            if existing is None:
                raise RuntimeError("idempotent command disappeared")
            if existing.request_hash != request_hash:
                raise OperationCommandError("幂等键已被其他请求使用")
            if existing.status != "ACCEPTED" or existing.completed_at is None:
                raise OperationCommandError("相同命令仍在处理中")
            return _command_result(existing)

        result = await apply(now)
        command = await self._session.get(OperationCommand, command_id)
        if command is None:
            raise RuntimeError("operation command could not be reloaded")
        command.status = "ACCEPTED"
        command.result = result
        command.completed_at = now
        await self._session.commit()
        structlog.get_logger("operations.audit").info(
            "admin_command_accepted",
            command_id=str(command_id),
            action=action,
            target_type=target_type,
            target_id=target_id,
            actor=context.actor,
            reason=reason,
            result=result,
            client_ip=context.client_ip,
        )
        return _command_result(command)

    def _resolve_specs(
        self,
        api_names: Sequence[str],
        *,
        daily_only: bool,
    ) -> tuple[ApiSpec, ...]:
        if len(set(api_names)) != len(api_names):
            raise OperationCommandError("接口列表不能重复", status_code=422)
        specs: list[ApiSpec] = []
        for api_name in api_names:
            try:
                spec = self._api_specs.get(api_name)
            except KeyError as exc:
                raise OperationCommandError(
                    f"未启用的采集接口：{api_name}", status_code=422
                ) from exc
            if daily_only and spec.schedule_group not in {
                ScheduleGroup.DAILY,
                ScheduleGroup.DELAYED,
                ScheduleGroup.HOT,
            }:
                raise OperationCommandError(
                    f"历史回填只允许按业务日期采集的接口：{api_name}", status_code=422
                )
            specs.append(spec)
        return tuple(specs)

    @staticmethod
    def _scheduled_job(job_id: str) -> ScheduledJobDefinition:
        definition = SCHEDULED_JOB_BY_ID.get(job_id)
        if definition is None:
            raise OperationCommandError("定时任务不存在", status_code=404)
        return definition

    async def _trading_dates(self, start_date: date, end_date: date) -> tuple[date, ...]:
        expected_days = (end_date - start_date).days + 1
        covered_days = await self._session.scalar(
            select(func.count())
            .select_from(TradeCalendar)
            .where(
                TradeCalendar.exchange == "SSE",
                TradeCalendar.cal_date.between(start_date, end_date),
            )
        )
        if int(covered_days or 0) != expected_days:
            raise OperationCommandError("交易日历未完整覆盖回填日期范围")
        rows = await self._session.scalars(
            select(TradeCalendar.cal_date)
            .where(
                TradeCalendar.exchange == "SSE",
                TradeCalendar.cal_date.between(start_date, end_date),
                TradeCalendar.is_open.is_(True),
            )
            .order_by(TradeCalendar.cal_date)
        )
        return tuple(rows)

    async def _create_batch(
        self,
        *,
        batch_type: BatchType,
        business_date: date | None,
        specs: Sequence[ApiSpec],
        now: datetime,
    ) -> UUID:
        task_values: list[dict[str, object]] = []
        for spec in specs:
            try:
                scopes = await self._resolve_scopes(spec, business_date)
            except ValueError as exc:
                raise OperationCommandError(str(exc), status_code=422) from exc
            for scope in scopes:
                task_values.append(
                    {
                        "task_id": uuid4(),
                        "provider": spec.provider,
                        "api_name": spec.api_name,
                        "scope_key": scope.scope_key,
                        "request_params": _json_params(scope.params),
                        "max_attempts": spec.retry_policy.max_attempts,
                    }
                )
        if not task_values:
            raise OperationCommandError("所选接口没有生成采集范围", status_code=422)
        task_values.sort(key=lambda item: (str(item["api_name"]), str(item["scope_key"])))
        batch_id = uuid4()
        self._session.add(
            CollectionBatch(
                batch_id=batch_id,
                batch_type=batch_type.value,
                business_date=business_date,
                status=BatchStatus.PENDING.value,
                scheduled_at=now,
                plan_version=_plan_version(task_values),
                expected_task_count=len(task_values),
                planning_completed_at=now,
            )
        )
        self._session.add_all(
            CollectionTask(
                batch_id=batch_id,
                status=CollectionTaskStatus.PENDING.value,
                **task,
            )
            for task in task_values
        )
        await self._session.flush()
        return batch_id

    async def _resolve_scopes(
        self,
        spec: ApiSpec,
        business_date: date | None,
    ) -> tuple[RequestScope, ...]:
        if spec.api_name == "ths_member":
            concept_codes = tuple(
                await self._session.scalars(
                    select(ConceptBoard.ts_code)
                    .where(ConceptBoard.source == "THS")
                    .order_by(ConceptBoard.ts_code)
                )
            )
            theme_codes = tuple(
                await self._session.scalars(
                    select(ThemeIndex.ts_code)
                    .where(ThemeIndex.source == "THS")
                    .order_by(ThemeIndex.ts_code)
                )
            )
            codes = tuple(sorted({*concept_codes, *theme_codes}))
            if not codes:
                raise OperationCommandError("同花顺概念和主题主数据尚未发布，不能采集板块成分")
            return ths_member_scopes(codes)
        if spec.api_name == "dc_concept_cons":
            if business_date is None:
                raise OperationCommandError("题材成分采集必须指定业务日期")
            theme_codes = tuple(
                await self._session.scalars(
                    select(MarketThemeDaily.theme_code)
                    .where(
                        MarketThemeDaily.source == "DC",
                        MarketThemeDaily.trade_date == business_date,
                    )
                    .order_by(MarketThemeDaily.theme_code)
                )
            )
            if not theme_codes:
                raise OperationCommandError(
                    "该日期题材主数据尚未发布，必须先完成dc_concept采集和加工"
                )
            return theme_member_scopes(business_date, theme_codes)
        return tuple(spec.scope_builder(business_date))

    async def _create_batch_from_task(
        self,
        task: CollectionTask,
        source_batch: CollectionBatch,
        *,
        now: datetime,
    ) -> UUID:
        task_values = [
            {
                "task_id": uuid4(),
                "provider": task.provider,
                "api_name": task.api_name,
                "scope_key": task.scope_key,
                "request_params": dict(task.request_params),
                "max_attempts": task.max_attempts,
            }
        ]
        batch_id = uuid4()
        self._session.add(
            CollectionBatch(
                batch_id=batch_id,
                batch_type=BatchType.REPAIR.value,
                business_date=source_batch.business_date,
                status=BatchStatus.PENDING.value,
                scheduled_at=now,
                plan_version=_plan_version(task_values),
                expected_task_count=1,
                planning_completed_at=now,
            )
        )
        self._session.add(
            CollectionTask(
                batch_id=batch_id,
                status=CollectionTaskStatus.PENDING.value,
                **task_values[0],
            )
        )
        await self._session.flush()
        return batch_id

    async def _block_processing_downstream(self, process_id: UUID, reason: str) -> None:
        dependencies = tuple(
            await self._session.scalars(
                select(ProcessingDependency)
                .where(ProcessingDependency.resolved_release_process_id == process_id)
                .with_for_update()
            )
        )
        for dependency in dependencies:
            dependency.status = DependencyStatus.FAILED.value
            dependency.blocked_reason = reason
            await self._session.execute(
                update(ProcessingTask)
                .where(
                    ProcessingTask.process_id == dependency.process_id,
                    ProcessingTask.status.in_(PROCESSING_MUTABLE),
                )
                .values(
                    status=ProcessingTaskStatus.BLOCKED.value,
                    error_message=f"上游加工被人工终止：{reason}",
                )
            )


class CommandContext:
    def __init__(
        self,
        *,
        idempotency_key: str,
        actor: str,
        request_id: str,
        client_ip: str | None,
    ) -> None:
        self.idempotency_key = idempotency_key
        self.actor = actor
        self.request_id = request_id
        self.client_ip = client_ip


def _request_hash(
    action: str,
    target_type: str,
    target_id: str | None,
    payload: dict[str, object],
) -> str:
    value = json.dumps(
        {
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(value.encode()).hexdigest()


def _plan_version(rows: Sequence[dict[str, object]]) -> str:
    payload = [
        {
            "provider": row["provider"],
            "api_name": row["api_name"],
            "scope_key": row["scope_key"],
            "request_params": row["request_params"],
            "max_attempts": row["max_attempts"],
        }
        for row in rows
    ]
    value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(value.encode()).hexdigest()


def _json_params(params: Mapping[str, ParameterValue]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, date):
            result[str(key)] = value.strftime("%Y%m%d")
        elif isinstance(value, tuple):
            result[str(key)] = list(value)
        else:
            result[str(key)] = value
    return result


def _command_result(command: OperationCommand) -> OperationCommandResult:
    if command.completed_at is None:
        raise RuntimeError("accepted command has no completion time")
    return OperationCommandResult(
        command_id=str(command.command_id),
        action=command.action,
        target_type=command.target_type,
        target_id=command.target_id,
        status="accepted",
        result=dict(command.result),
        created_at=command.created_at.astimezone(UTC),
        completed_at=command.completed_at.astimezone(UTC),
    )


def _verb(action: str) -> str:
    return "跳过" if action.startswith("SKIP_") else "取消"
