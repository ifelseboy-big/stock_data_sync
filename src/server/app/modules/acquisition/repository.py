import json
from collections.abc import Callable, Collection, Sequence
from datetime import date, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.common.errors import BatchPlanningError, ClosedBatchPlanMismatchError
from app.modules.acquisition.domain import (
    TERMINAL_TASK_STATUSES,
    AssetSnapshot,
    BatchPlanResult,
    ClaimedCollectionTask,
    RunningTaskSnapshot,
    TaskBlueprint,
    TaskTransition,
)
from app.modules.acquisition.models import (
    BatchStatus,
    BatchType,
    CollectionBatch,
    CollectionTask,
    CollectionTaskStatus,
    RawDataAsset,
)
from app.modules.stocks.models import TradeCalendar
from app.storage import RawAssetMetadata

type SessionFactory = Callable[[], Session]


class AcquisitionRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def create_or_get_batch(
        self,
        *,
        batch_type: BatchType,
        business_date: date | None,
        scheduled_at: datetime,
    ) -> UUID:
        with self._session_factory() as session, session.begin():
            batch_id = uuid4()
            session.execute(
                insert(CollectionBatch)
                .values(
                    batch_id=batch_id,
                    batch_type=batch_type.value,
                    business_date=business_date,
                    scheduled_at=scheduled_at,
                    status=BatchStatus.PENDING.value,
                )
                .on_conflict_do_nothing(
                    index_elements=(
                        CollectionBatch.batch_type,
                        CollectionBatch.business_date,
                        CollectionBatch.scheduled_at,
                    )
                )
            )
            existing_id = session.scalar(
                select(CollectionBatch.batch_id).where(
                    CollectionBatch.batch_type == batch_type.value,
                    CollectionBatch.business_date.is_(business_date)
                    if business_date is None
                    else CollectionBatch.business_date == business_date,
                    CollectionBatch.scheduled_at == scheduled_at,
                )
            )
            if existing_id is None:
                raise BatchPlanningError("batch could not be created or resolved")
            return existing_id

    def append_tasks(
        self,
        batch_id: UUID,
        blueprints: Sequence[TaskBlueprint],
        *,
        finalize: bool,
        now: datetime,
    ) -> BatchPlanResult:
        with self._session_factory() as session, session.begin():
            batch = session.scalar(
                select(CollectionBatch)
                .where(CollectionBatch.batch_id == batch_id)
                .with_for_update()
            )
            if batch is None:
                raise BatchPlanningError(f"unknown collection batch: {batch_id}")
            if batch.status == BatchStatus.CANCELLED.value:
                raise BatchPlanningError("cancelled batches cannot accept tasks")
            if batch.status == BatchStatus.CLOSED.value:
                return self._replay_closed_plan(
                    session,
                    batch=batch,
                    blueprints=blueprints,
                )

            existing_keys = set(
                session.execute(
                    select(CollectionTask.api_name, CollectionTask.scope_key).where(
                        CollectionTask.batch_id == batch_id
                    )
                ).all()
            )
            submitted_keys = {(item.api_name, item.scope_key) for item in blueprints}
            if len(submitted_keys) != len(blueprints):
                raise BatchPlanningError("planned tasks contain duplicate api_name/scope_key pairs")
            if batch.planning_completed_at is not None and not submitted_keys <= existing_keys:
                raise BatchPlanningError("a frozen batch cannot accept new tasks")

            created_task_count = 0
            for blueprint in blueprints:
                result = session.execute(
                    insert(CollectionTask)
                    .values(
                        task_id=uuid4(),
                        batch_id=batch_id,
                        provider=blueprint.provider,
                        api_name=blueprint.api_name,
                        scope_key=blueprint.scope_key,
                        request_params=blueprint.request_params,
                        max_attempts=blueprint.max_attempts,
                        status=CollectionTaskStatus.PENDING.value,
                    )
                    .on_conflict_do_nothing(constraint="uq_collection_task_batch_api_scope")
                    .returning(CollectionTask.task_id)
                ).scalar_one_or_none()
                created_task_count += int(result is not None)

            planned_rows = session.execute(
                select(
                    CollectionTask.provider,
                    CollectionTask.api_name,
                    CollectionTask.scope_key,
                    CollectionTask.request_params,
                    CollectionTask.max_attempts,
                )
                .where(CollectionTask.batch_id == batch_id)
                .order_by(CollectionTask.api_name, CollectionTask.scope_key)
            ).all()
            total_task_count = len(planned_rows)
            plan_version = batch.plan_version

            if finalize:
                calculated_version = _plan_version(planned_rows)
                if batch.planning_completed_at is not None:
                    if (
                        batch.plan_version != calculated_version
                        or batch.expected_task_count != total_task_count
                    ):
                        raise BatchPlanningError("frozen batch plan does not match persisted tasks")
                else:
                    batch.plan_version = calculated_version
                    batch.expected_task_count = total_task_count
                    batch.planning_completed_at = now
                plan_version = calculated_version

            return BatchPlanResult(
                batch_id=batch_id,
                created_task_count=created_task_count,
                total_task_count=total_task_count,
                frozen=batch.planning_completed_at is not None or finalize,
                plan_version=plan_version,
            )

    @staticmethod
    def _replay_closed_plan(
        session: Session,
        *,
        batch: CollectionBatch,
        blueprints: Sequence[TaskBlueprint],
    ) -> BatchPlanResult:
        submitted_keys = {(item.api_name, item.scope_key) for item in blueprints}
        if len(submitted_keys) != len(blueprints):
            raise BatchPlanningError("planned tasks contain duplicate api_name/scope_key pairs")
        persisted_rows = session.execute(
            select(
                CollectionTask.provider,
                CollectionTask.api_name,
                CollectionTask.scope_key,
                CollectionTask.request_params,
                CollectionTask.max_attempts,
            ).where(CollectionTask.batch_id == batch.batch_id)
        ).all()
        persisted = {
            (row.api_name, row.scope_key): (
                row.provider,
                dict(row.request_params),
                row.max_attempts,
            )
            for row in persisted_rows
        }
        for blueprint in blueprints:
            key = (blueprint.api_name, blueprint.scope_key)
            expected = (
                blueprint.provider,
                dict(blueprint.request_params),
                blueprint.max_attempts,
            )
            if persisted.get(key) != expected:
                raise ClosedBatchPlanMismatchError(
                    "closed batches only accept an exact replay of persisted tasks"
                )
        return BatchPlanResult(
            batch_id=batch.batch_id,
            created_task_count=0,
            total_task_count=len(persisted_rows),
            frozen=True,
            plan_version=batch.plan_version,
        )

    def is_trading_day(self, business_date: date, *, exchange: str = "SSE") -> bool | None:
        with self._session_factory() as session:
            return session.scalar(
                select(TradeCalendar.is_open).where(
                    TradeCalendar.exchange == exchange,
                    TradeCalendar.cal_date == business_date,
                )
            )

    def claim_next(
        self,
        *,
        allowed_batch_types: Collection[BatchType],
        now: datetime,
    ) -> ClaimedCollectionTask | None:
        allowed_values = tuple(item.value for item in allowed_batch_types)
        if not allowed_values:
            return None

        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(CollectionTask, CollectionBatch)
                .join(CollectionBatch, CollectionBatch.batch_id == CollectionTask.batch_id)
                .where(
                    CollectionBatch.status.in_(
                        (BatchStatus.PENDING.value, BatchStatus.RUNNING.value)
                    ),
                    CollectionBatch.batch_type.in_(allowed_values),
                    or_(
                        CollectionTask.status == CollectionTaskStatus.PENDING.value,
                        (CollectionTask.status == CollectionTaskStatus.RETRY_WAIT.value)
                        & (CollectionTask.next_retry_at.is_not(None))
                        & (CollectionTask.next_retry_at <= now),
                    ),
                )
                .order_by(
                    case(
                        (CollectionBatch.batch_type == BatchType.REPAIR.value, 50),
                        (
                            CollectionBatch.batch_type.in_(
                                (
                                    BatchType.DAILY.value,
                                    BatchType.MASTER.value,
                                    BatchType.HOT.value,
                                    BatchType.DELAYED.value,
                                )
                            ),
                            100,
                        ),
                        (CollectionBatch.batch_type == BatchType.BACKFILL.value, 400),
                        else_=500,
                    ),
                    case(
                        (
                            CollectionBatch.batch_type == BatchType.BACKFILL.value,
                            CollectionBatch.business_date,
                        ),
                        else_=None,
                    )
                    .desc()
                    .nulls_last(),
                    CollectionBatch.scheduled_at,
                    CollectionTask.api_name,
                    CollectionTask.scope_key,
                    CollectionTask.task_id,
                )
                .with_for_update(skip_locked=True, of=CollectionTask)
                .limit(1)
            ).first()
            if row is None:
                return None

            task, batch = row
            task.status = CollectionTaskStatus.RUNNING.value
            task.attempt_count += 1
            task.next_retry_at = None
            task.started_at = now
            task.finished_at = None
            task.error_code = None
            task.error_message = None
            if batch.status == BatchStatus.PENDING.value:
                batch.status = BatchStatus.RUNNING.value
                batch.started_at = batch.started_at or now

            return ClaimedCollectionTask(
                task_id=task.task_id,
                batch_id=batch.batch_id,
                batch_type=BatchType(batch.batch_type),
                business_date=batch.business_date,
                provider=task.provider,
                api_name=task.api_name,
                scope_key=task.scope_key,
                request_params=dict(task.request_params),
                attempt_count=task.attempt_count,
                max_attempts=task.max_attempts,
            )

    def complete_task(
        self,
        task: ClaimedCollectionTask | RunningTaskSnapshot,
        metadata: RawAssetMetadata,
        *,
        request_count: int,
        empty: bool,
        completed_at: datetime,
    ) -> TaskTransition:
        status = CollectionTaskStatus.EMPTY_VALID if empty else CollectionTaskStatus.SUCCESS
        with self._session_factory() as session, session.begin():
            task_model = session.scalar(
                select(CollectionTask)
                .where(CollectionTask.task_id == task.task_id)
                .with_for_update()
            )
            if task_model is None:
                raise RuntimeError(f"unknown collection task: {task.task_id}")

            session.execute(
                insert(RawDataAsset)
                .values(
                    asset_id=uuid4(),
                    task_id=task.task_id,
                    provider=task.provider,
                    api_name=task.api_name,
                    business_date=task.business_date,
                    request_params=task.request_params,
                    storage_uri=metadata.storage_uri,
                    content_hash=metadata.content_hash,
                    schema_fingerprint=metadata.schema_fingerprint,
                    row_count=metadata.row_count,
                    is_complete=True,
                    fetched_at=completed_at,
                    sealed_at=completed_at,
                )
                .on_conflict_do_nothing(index_elements=[RawDataAsset.task_id])
            )
            persisted_asset = session.scalar(
                select(RawDataAsset).where(RawDataAsset.task_id == task.task_id)
            )
            if persisted_asset is None or not _asset_matches(persisted_asset, metadata):
                raise RuntimeError("persisted raw asset does not match the sealed file")

            task_model.status = status.value
            task_model.request_count += request_count
            task_model.row_count = metadata.row_count
            task_model.next_retry_at = None
            task_model.finished_at = completed_at
            task_model.error_code = None
            task_model.error_message = None
            return TaskTransition(task.task_id, status, None)

    def fail_task(
        self,
        task_id: UUID,
        *,
        error_code: str,
        error_message: str,
        request_count: int,
        retry_at: datetime | None,
        completed_at: datetime,
        skipped: bool = False,
    ) -> TaskTransition:
        with self._session_factory() as session, session.begin():
            task = session.scalar(
                select(CollectionTask).where(CollectionTask.task_id == task_id).with_for_update()
            )
            if task is None:
                raise RuntimeError(f"unknown collection task: {task_id}")
            if task.status in TERMINAL_TASK_STATUSES:
                return TaskTransition(
                    task_id, CollectionTaskStatus(task.status), task.next_retry_at
                )

            if skipped:
                status = CollectionTaskStatus.SKIPPED
                retry_at = None
            elif retry_at is not None and task.attempt_count < task.max_attempts:
                status = CollectionTaskStatus.RETRY_WAIT
            else:
                status = CollectionTaskStatus.FAILED
                retry_at = None

            task.status = status.value
            task.request_count += request_count
            task.next_retry_at = retry_at
            task.finished_at = (
                completed_at
                if status
                in {
                    CollectionTaskStatus.FAILED,
                    CollectionTaskStatus.SKIPPED,
                }
                else None
            )
            task.error_code = error_code[:64]
            task.error_message = error_message
            return TaskTransition(task_id, status, retry_at)

    def close_ready_batches(self, *, now: datetime) -> tuple[UUID, ...]:
        closed_ids: list[UUID] = []
        with self._session_factory() as session, session.begin():
            batches = session.scalars(
                select(CollectionBatch)
                .where(
                    CollectionBatch.status.in_(
                        (BatchStatus.PENDING.value, BatchStatus.RUNNING.value)
                    ),
                    CollectionBatch.planning_completed_at.is_not(None),
                )
                .order_by(CollectionBatch.scheduled_at)
                .with_for_update(skip_locked=True)
            ).all()
            for batch in batches:
                total_count = session.scalar(
                    select(func.count())
                    .select_from(CollectionTask)
                    .where(CollectionTask.batch_id == batch.batch_id)
                )
                active_count = session.scalar(
                    select(func.count())
                    .select_from(CollectionTask)
                    .where(
                        CollectionTask.batch_id == batch.batch_id,
                        CollectionTask.status.not_in(TERMINAL_TASK_STATUSES),
                    )
                )
                if total_count != batch.expected_task_count or active_count != 0:
                    continue
                batch.status = BatchStatus.CLOSED.value
                batch.closed_at = now
                closed_ids.append(batch.batch_id)
        return tuple(closed_ids)

    def running_tasks(self) -> tuple[RunningTaskSnapshot, ...]:
        with self._session_factory() as session:
            rows = session.execute(
                select(CollectionTask, CollectionBatch)
                .join(CollectionBatch, CollectionBatch.batch_id == CollectionTask.batch_id)
                .where(CollectionTask.status == CollectionTaskStatus.RUNNING.value)
            ).all()
            return tuple(
                RunningTaskSnapshot(
                    task_id=task.task_id,
                    batch_id=task.batch_id,
                    business_date=batch.business_date,
                    provider=task.provider,
                    api_name=task.api_name,
                    request_params=dict(task.request_params),
                    attempt_count=task.attempt_count,
                    max_attempts=task.max_attempts,
                    started_at=task.started_at,
                )
                for task, batch in rows
            )

    def assets(self) -> tuple[AssetSnapshot, ...]:
        with self._session_factory() as session:
            return tuple(
                AssetSnapshot(
                    task_id=item.task_id,
                    storage_uri=item.storage_uri,
                    content_hash=item.content_hash,
                    schema_fingerprint=item.schema_fingerprint,
                    row_count=item.row_count,
                )
                for item in session.scalars(select(RawDataAsset))
            )

    def asset_for_task(self, task_id: UUID) -> AssetSnapshot | None:
        with self._session_factory() as session:
            item = session.scalar(select(RawDataAsset).where(RawDataAsset.task_id == task_id))
            if item is None:
                return None
            return AssetSnapshot(
                task_id=item.task_id,
                storage_uri=item.storage_uri,
                content_hash=item.content_hash,
                schema_fingerprint=item.schema_fingerprint,
                row_count=item.row_count,
            )

    def mark_asset_missing(self, task_id: UUID, *, now: datetime) -> None:
        with self._session_factory() as session, session.begin():
            task = session.scalar(
                select(CollectionTask).where(CollectionTask.task_id == task_id).with_for_update()
            )
            if task is None:
                return
            task.status = CollectionTaskStatus.FAILED.value
            task.next_retry_at = None
            task.finished_at = now
            task.error_code = "ASSET_MISSING"
            task.error_message = "raw asset database record exists but file is missing"


def _plan_version(rows: Sequence[Any]) -> str:
    payload = [
        {
            "provider": row.provider,
            "api_name": row.api_name,
            "scope_key": row.scope_key,
            "request_params": row.request_params,
            "max_attempts": row.max_attempts,
        }
        for row in rows
    ]
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode()).hexdigest()


def _asset_matches(asset: RawDataAsset, metadata: RawAssetMetadata) -> bool:
    return (
        asset.storage_uri == metadata.storage_uri
        and asset.content_hash == metadata.content_hash
        and asset.schema_fingerprint == metadata.schema_fingerprint
        and asset.row_count == metadata.row_count
        and asset.is_complete
    )
