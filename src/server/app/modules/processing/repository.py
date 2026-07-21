from ast import literal_eval
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from uuid import UUID, uuid5

import structlog
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.catalog import DatasetSpec, DependencyKind, ReleaseScope
from app.modules.acquisition.domain import TERMINAL_TASK_STATUSES
from app.modules.acquisition.models import (
    BatchStatus,
    BatchType,
    CollectionBatch,
    CollectionTask,
    CollectionTaskStatus,
    RawDataAsset,
)
from app.modules.partitions.service import ensure_monthly_partitions
from app.modules.processing.domain import (
    ClaimedProcessingTask,
    ProcessingPlanResult,
    ProcessingTransition,
    RawDependencyAsset,
)
from app.modules.processing.models import (
    DatasetRelease,
    DependencyStatus,
    DependencyType,
    ProcessingDependency,
    ProcessingTask,
    ProcessingTaskStatus,
)
from app.modules.processing.processors.base import DatasetProcessor, PreparedDataset
from app.modules.stocks.models import Stock

type SessionFactory = Callable[[], Session]

PROCESSING_VERSION_NAMESPACE = UUID("24f4614f-5c65-59af-9955-bc7352d39d51")
PROCESSING_TERMINAL_STATUSES = frozenset(
    {
        ProcessingTaskStatus.SUCCESS.value,
        ProcessingTaskStatus.FAILED.value,
        ProcessingTaskStatus.SKIPPED.value,
        ProcessingTaskStatus.CANCELLED.value,
    }
)
UNKNOWN_STOCKS_ERROR_PREFIX = "dataset references unknown stocks:"


class ProcessingRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def plan_closed_batches(
        self,
        dataset_specs: Sequence[DatasetSpec],
        *,
        now: datetime,
        source_batch_ids: Sequence[UUID] | None = None,
    ) -> ProcessingPlanResult:
        scanned_batch_count = 0
        created_task_count = 0
        queued_task_count = 0
        blocked_task_count = 0
        with self._session_factory() as session, session.begin():
            batch_statement = select(CollectionBatch).where(
                CollectionBatch.status == BatchStatus.CLOSED.value
            )
            if source_batch_ids is not None:
                if not source_batch_ids:
                    return ProcessingPlanResult(0, 0, 0, 0)
                batch_statement = batch_statement.where(
                    CollectionBatch.batch_id.in_(source_batch_ids)
                )
            batches = session.scalars(
                batch_statement.order_by(CollectionBatch.closed_at, CollectionBatch.batch_id)
            ).all()
            for batch in batches:
                scanned_batch_count += 1
                batch_api_names = set(
                    session.scalars(
                        select(CollectionTask.api_name)
                        .where(CollectionTask.batch_id == batch.batch_id)
                        .distinct()
                    )
                )
                eligible_specs = _affected_dataset_specs(dataset_specs, batch_api_names)
                planned_tasks: list[tuple[DatasetSpec, UUID]] = []
                created_for_batch = False
                for spec in eligible_specs:
                    if not (
                        _raw_dependency_names(spec) & batch_api_names
                    ) and not self._has_all_raw_dependencies(
                        session,
                        source_batch=batch,
                        spec=spec,
                    ):
                        continue
                    process_id, created = self._upsert_processing_task(
                        session,
                        batch=batch,
                        spec=spec,
                    )
                    created_task_count += int(created)
                    created_for_batch = created_for_batch or created
                    planned_tasks.append((spec, process_id))

                if created_for_batch and batch.business_date is not None:
                    ensure_monthly_partitions(
                        session.connection(),
                        reference_date=batch.business_date,
                        months_ahead=0,
                    )

                for spec, process_id in planned_tasks:
                    task = session.scalar(
                        select(ProcessingTask)
                        .where(ProcessingTask.process_id == process_id)
                        .with_for_update()
                    )
                    if task is None or task.status in PROCESSING_TERMINAL_STATUSES:
                        continue
                    if task.status in {
                        ProcessingTaskStatus.RUNNING.value,
                        ProcessingTaskStatus.RETRY_WAIT.value,
                    }:
                        continue
                    self._resolve_dependencies(
                        session,
                        task=task,
                        spec=spec,
                        source_batch=batch,
                    )
                    status = self._refresh_task_readiness(session, task, now=now)
                    queued_task_count += int(status == ProcessingTaskStatus.QUEUED)
                    blocked_task_count += int(status == ProcessingTaskStatus.BLOCKED)

        return ProcessingPlanResult(
            scanned_batch_count=scanned_batch_count,
            created_task_count=created_task_count,
            queued_task_count=queued_task_count,
            blocked_task_count=blocked_task_count,
        )

    def _has_all_raw_dependencies(
        self,
        session: Session,
        *,
        source_batch: CollectionBatch,
        spec: DatasetSpec,
    ) -> bool:
        raw_dependencies = tuple(
            dependency
            for dependency in spec.dependencies
            if dependency.kind == DependencyKind.RAW_ASSET
        )
        return bool(raw_dependencies) and all(
            self._latest_raw_assets(
                session,
                source_batch=source_batch,
                api_name=dependency.name,
                scope=dependency.scope,
                business_date=source_batch.business_date,
            )
            for dependency in raw_dependencies
        )

    def claim_next(
        self,
        *,
        now: datetime,
        advisory_lock_id: int,
        max_running_tasks: int = 1,
        source_batch_ids: Sequence[UUID] | None = None,
    ) -> ClaimedProcessingTask | None:
        if max_running_tasks < 1:
            raise ValueError("max_running_tasks must be positive")
        with self._session_factory() as session, session.begin():
            lock_acquired = session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
                {"lock_id": advisory_lock_id},
            )
            if not lock_acquired:
                return None
            running_count = session.scalar(
                select(func.count())
                .select_from(ProcessingTask)
                .where(ProcessingTask.status == ProcessingTaskStatus.RUNNING.value)
            )
            if int(running_count or 0) >= max_running_tasks:
                return None
            running_datasets = select(ProcessingTask.output_dataset).where(
                ProcessingTask.status == ProcessingTaskStatus.RUNNING.value
            )
            task_statement = select(ProcessingTask).where(
                or_(
                    ProcessingTask.status == ProcessingTaskStatus.QUEUED.value,
                    (ProcessingTask.status == ProcessingTaskStatus.RETRY_WAIT.value)
                    & (ProcessingTask.next_retry_at.is_not(None))
                    & (ProcessingTask.next_retry_at <= now),
                ),
                ProcessingTask.output_dataset.not_in(running_datasets),
            )
            if source_batch_ids is not None:
                if not source_batch_ids:
                    return None
                task_statement = task_statement.where(
                    ProcessingTask.source_batch_id.in_(source_batch_ids)
                )
            task = session.scalar(
                task_statement.order_by(
                    ProcessingTask.priority,
                    ProcessingTask.business_date.asc().nullsfirst(),
                    ProcessingTask.queued_at,
                    ProcessingTask.process_id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if task is None:
                return None
            task.status = ProcessingTaskStatus.RUNNING.value
            task.attempt_count += 1
            task.next_retry_at = None
            task.started_at = now
            task.finished_at = None
            task.error_message = None
            task.warning_message = None
            return ClaimedProcessingTask(
                process_id=task.process_id,
                source_batch_id=task.source_batch_id,
                process_type=task.process_type,
                business_date=task.business_date,
                output_dataset=task.output_dataset,
                output_version=task.output_version,
                attempt_count=task.attempt_count,
                max_attempts=task.max_attempts,
            )

    def raw_dependencies(self, process_id: UUID) -> tuple[RawDependencyAsset, ...]:
        with self._session_factory() as session:
            dependencies = session.execute(
                select(ProcessingDependency, RawDataAsset)
                .join(RawDataAsset, RawDataAsset.asset_id == ProcessingDependency.resolved_asset_id)
                .where(
                    ProcessingDependency.process_id == process_id,
                    ProcessingDependency.dependency_type == DependencyType.RAW_ASSET.value,
                    ProcessingDependency.status == DependencyStatus.READY.value,
                )
                .order_by(
                    ProcessingDependency.dependency_name,
                    ProcessingDependency.dependency_scope_key,
                )
            ).all()
            return tuple(
                RawDependencyAsset(
                    dependency_name=dependency.dependency_name,
                    scope_key=dependency.dependency_scope_key,
                    asset_id=asset.asset_id,
                    storage_uri=asset.storage_uri,
                    content_hash=asset.content_hash,
                    schema_fingerprint=asset.schema_fingerprint,
                    row_count=asset.row_count,
                )
                for dependency, asset in dependencies
            )

    def publish_success(
        self,
        task: ClaimedProcessingTask,
        spec: DatasetSpec,
        *,
        prepared: PreparedDataset,
        processor: DatasetProcessor,
        published_at: datetime,
        rows_read: int,
        rows_rejected: int,
    ) -> ProcessingTransition:
        with self._session_factory() as session, session.begin():
            process = session.scalar(
                select(ProcessingTask)
                .where(ProcessingTask.process_id == task.process_id)
                .with_for_update()
            )
            if process is None or process.status != ProcessingTaskStatus.RUNNING.value:
                raise RuntimeError("processing task is not in RUNNING state")
            publication = processor.write(session, prepared, published_at=published_at)
            rows_written = publication.rows_written
            scope_key = _release_scope_key(spec.release_scope, task.business_date)
            release_values = {
                "dataset_name": spec.dataset_name,
                "scope_type": spec.release_scope.value,
                "scope_key": scope_key,
                "business_date": (
                    None if spec.release_scope == ReleaseScope.GLOBAL else task.business_date
                ),
                "version_id": task.output_version,
                "process_id": task.process_id,
                "row_count": rows_written,
                "published_at": published_at,
            }
            session.execute(
                insert(DatasetRelease)
                .values(**release_values)
                .on_conflict_do_update(
                    index_elements=(
                        DatasetRelease.dataset_name,
                        DatasetRelease.scope_type,
                        DatasetRelease.scope_key,
                    ),
                    set_=release_values,
                )
            )
            process.status = ProcessingTaskStatus.SUCCESS.value
            process.finished_at = published_at
            process.next_retry_at = None
            process.rows_read = rows_read
            process.rows_rejected = rows_rejected + publication.rows_rejected
            process.rows_written = rows_written
            process.error_message = None
            process.warning_message = "\n".join(prepared.warning_messages)[:4000] or None
            self._resolve_downstream_after_success(session, process.process_id, published_at)
            if spec.dataset_name == "stock":
                recovered_count = self._recover_resolved_unknown_stock_tasks(
                    session,
                    stock_release_process_id=process.process_id,
                    now=published_at,
                )
                if recovered_count:
                    structlog.get_logger("processing_repository").info(
                        "unknown_stock_tasks_requeued",
                        recovered_count=recovered_count,
                        stock_release_process_id=str(process.process_id),
                    )
            return ProcessingTransition(
                task.process_id,
                ProcessingTaskStatus.SUCCESS,
                None,
            )

    @staticmethod
    def _recover_resolved_unknown_stock_tasks(
        session: Session,
        *,
        stock_release_process_id: UUID,
        now: datetime,
    ) -> int:
        candidates = session.scalars(
            select(ProcessingTask)
            .where(
                ProcessingTask.status == ProcessingTaskStatus.FAILED.value,
                ProcessingTask.error_message.like(f"{UNKNOWN_STOCKS_ERROR_PREFIX}%"),
                ProcessingTask.finished_at >= now - timedelta(days=30),
            )
            .order_by(ProcessingTask.finished_at, ProcessingTask.process_id)
            .with_for_update(skip_locked=True)
        ).all()
        parsed = {
            task.process_id: parsed_codes
            for task in candidates
            if (parsed_codes := _unknown_stock_codes(task.error_message))
        }
        if not parsed:
            return 0

        referenced_codes = set().union(*parsed.values())
        available_codes = set(
            session.scalars(select(Stock.ts_code).where(Stock.ts_code.in_(referenced_codes)))
        )
        dependencies = session.scalars(
            select(ProcessingDependency)
            .where(ProcessingDependency.process_id.in_(tuple(parsed)))
            .with_for_update()
        ).all()
        dependencies_by_process: dict[UUID, list[ProcessingDependency]] = {}
        for dependency in dependencies:
            dependencies_by_process.setdefault(dependency.process_id, []).append(dependency)

        recovered_count = 0
        for task in candidates:
            required_codes = parsed.get(task.process_id)
            if not required_codes or not required_codes.issubset(available_codes):
                continue
            task_dependencies = dependencies_by_process.get(task.process_id, [])
            stock_dependencies = [
                dependency
                for dependency in task_dependencies
                if dependency.dependency_type == DependencyType.DATASET_RELEASE.value
                and dependency.dependency_name == "stock"
            ]
            if len(stock_dependencies) != 1:
                continue
            stock_dependency = stock_dependencies[0]
            stock_dependency.status = DependencyStatus.READY.value
            stock_dependency.resolved_release_process_id = stock_release_process_id
            stock_dependency.blocked_reason = None
            if any(
                dependency.status != DependencyStatus.READY.value
                for dependency in task_dependencies
            ):
                continue
            task.status = ProcessingTaskStatus.QUEUED.value
            task.max_attempts = max(task.max_attempts, task.attempt_count + 1)
            task.next_retry_at = None
            task.queued_at = now
            task.started_at = None
            task.finished_at = None
            task.error_message = None
            recovered_count += 1
        return recovered_count

    def fail_task(
        self,
        task: ClaimedProcessingTask,
        *,
        message: str,
        retryable: bool,
        now: datetime,
    ) -> ProcessingTransition:
        with self._session_factory() as session, session.begin():
            process = session.scalar(
                select(ProcessingTask)
                .where(ProcessingTask.process_id == task.process_id)
                .with_for_update()
            )
            if process is None:
                raise RuntimeError("unknown processing task")
            retry_at = None
            if retryable and process.attempt_count < process.max_attempts:
                retry_at = now + timedelta(seconds=min(30 * 2 ** (process.attempt_count - 1), 900))
                status = ProcessingTaskStatus.RETRY_WAIT
            else:
                status = ProcessingTaskStatus.FAILED
            process.status = status.value
            process.next_retry_at = retry_at
            process.finished_at = now if status == ProcessingTaskStatus.FAILED else None
            process.error_message = message
            if status == ProcessingTaskStatus.FAILED:
                self._block_downstream_after_failure(session, process.process_id, message)
            return ProcessingTransition(task.process_id, status, retry_at)

    def recover_running_tasks(
        self,
        *,
        now: datetime,
        started_before: datetime | None = None,
    ) -> int:
        with self._session_factory() as session, session.begin():
            statement = select(ProcessingTask).where(
                ProcessingTask.status == ProcessingTaskStatus.RUNNING.value
            )
            if started_before is not None:
                statement = statement.where(
                    ProcessingTask.started_at.is_not(None),
                    ProcessingTask.started_at < started_before,
                )
            tasks = session.scalars(statement.with_for_update(skip_locked=True)).all()
            for task in tasks:
                task.status = ProcessingTaskStatus.RETRY_WAIT.value
                task.attempt_count = max(task.attempt_count - 1, 0)
                task.next_retry_at = now
                task.started_at = None
                task.finished_at = None
                task.error_message = "scheduler stopped while processing task was running"
            return len(tasks)

    def _upsert_processing_task(
        self,
        session: Session,
        *,
        batch: CollectionBatch,
        spec: DatasetSpec,
    ) -> tuple[UUID, bool]:
        output_version = uuid5(
            PROCESSING_VERSION_NAMESPACE,
            f"{batch.batch_id}:{spec.dataset_name}:{spec.processor_version}:"
            f"{batch.business_date.isoformat() if batch.business_date else 'GLOBAL'}",
        )
        process_id = uuid5(PROCESSING_VERSION_NAMESPACE, f"process:{output_version}")
        created_id = session.execute(
            insert(ProcessingTask)
            .values(
                process_id=process_id,
                source_batch_id=batch.batch_id,
                process_type=f"{spec.processor}@{spec.processor_version}",
                business_date=batch.business_date,
                output_dataset=spec.dataset_name,
                output_version=output_version,
                status=ProcessingTaskStatus.WAITING_DEPENDENCY.value,
                priority=_priority_for_batch(BatchType(batch.batch_type)),
                max_attempts=spec.max_attempts,
            )
            .on_conflict_do_nothing(index_elements=(ProcessingTask.output_version,))
            .returning(ProcessingTask.process_id)
        ).scalar_one_or_none()
        return process_id, created_id is not None

    def _resolve_dependencies(
        self,
        session: Session,
        *,
        task: ProcessingTask,
        spec: DatasetSpec,
        source_batch: CollectionBatch,
    ) -> None:
        for dependency in spec.dependencies:
            if dependency.kind == DependencyKind.RAW_ASSET:
                current_rows = session.execute(
                    select(CollectionTask, RawDataAsset)
                    .outerjoin(RawDataAsset, RawDataAsset.task_id == CollectionTask.task_id)
                    .where(
                        CollectionTask.batch_id == task.source_batch_id,
                        CollectionTask.api_name == dependency.name,
                    )
                    .order_by(CollectionTask.scope_key)
                ).all()
                current_scope_keys = {
                    collection_task.scope_key for collection_task, _asset in current_rows
                }
                latest_rows = (
                    []
                    if current_rows and not dependency.merge_previous_scopes
                    else self._latest_raw_assets(
                        session,
                        source_batch=source_batch,
                        api_name=dependency.name,
                        scope=dependency.scope,
                        business_date=task.business_date,
                    )
                )
                selected_rows: list[tuple[CollectionTask, RawDataAsset | None]] = [
                    (collection_task, asset) for collection_task, asset in current_rows
                ]
                selected_rows.extend(
                    (collection_task, asset)
                    for collection_task, asset in latest_rows
                    if collection_task.scope_key not in current_scope_keys
                )
                if not selected_rows:
                    self._upsert_dependency(
                        session,
                        process_id=task.process_id,
                        dependency_type=DependencyType.RAW_ASSET,
                        dependency_name=dependency.name,
                        scope_key="__MISSING__",
                        scope={"release_scope": dependency.scope.value},
                        status=DependencyStatus.MISSING.value,
                        resolved_asset_id=None,
                        resolved_release_process_id=None,
                        blocked_reason="no complete raw asset was available when the batch closed",
                    )
                    continue
                for collection_task, asset in selected_rows:
                    ready = (
                        collection_task.status
                        in (
                            CollectionTaskStatus.SUCCESS.value,
                            CollectionTaskStatus.EMPTY_VALID.value,
                        )
                        and asset is not None
                        and asset.is_complete
                    )
                    status = (
                        DependencyStatus.READY.value
                        if ready
                        else DependencyStatus.FAILED.value
                        if collection_task.status in TERMINAL_TASK_STATUSES
                        else DependencyStatus.WAITING.value
                    )
                    self._upsert_dependency(
                        session,
                        process_id=task.process_id,
                        dependency_type=DependencyType.RAW_ASSET,
                        dependency_name=dependency.name,
                        scope_key=collection_task.scope_key,
                        scope=collection_task.request_params,
                        status=status,
                        resolved_asset_id=asset.asset_id if ready and asset is not None else None,
                        resolved_release_process_id=None,
                        blocked_reason=None if ready else collection_task.error_message,
                    )
            else:
                scope_key = _release_scope_key(dependency.scope, task.business_date)
                upstream = session.scalar(
                    select(ProcessingTask).where(
                        ProcessingTask.source_batch_id == task.source_batch_id,
                        ProcessingTask.output_dataset == dependency.name,
                    )
                )
                release = session.scalar(
                    select(DatasetRelease).where(
                        DatasetRelease.dataset_name == dependency.name,
                        DatasetRelease.scope_type == dependency.scope.value,
                        DatasetRelease.scope_key == scope_key,
                    )
                )
                resolved_process_id: UUID | None
                if upstream is not None:
                    resolved_process_id = upstream.process_id
                    status = (
                        DependencyStatus.READY.value
                        if upstream.status == ProcessingTaskStatus.SUCCESS.value
                        else DependencyStatus.FAILED.value
                        if upstream.status in PROCESSING_TERMINAL_STATUSES
                        else DependencyStatus.WAITING.value
                    )
                else:
                    resolved_process_id = release.process_id if release is not None else None
                    status = (
                        DependencyStatus.READY.value
                        if release is not None
                        else DependencyStatus.MISSING.value
                    )
                self._upsert_dependency(
                    session,
                    process_id=task.process_id,
                    dependency_type=DependencyType.DATASET_RELEASE,
                    dependency_name=dependency.name,
                    scope_key=scope_key,
                    scope={"scope_type": dependency.scope.value, "scope_key": scope_key},
                    status=status,
                    resolved_asset_id=None,
                    resolved_release_process_id=resolved_process_id,
                    blocked_reason=(
                        None if status == DependencyStatus.READY.value else "release unavailable"
                    ),
                )

    @staticmethod
    def _latest_raw_assets(
        session: Session,
        *,
        source_batch: CollectionBatch,
        api_name: str,
        scope: ReleaseScope,
        business_date: date | None,
    ) -> list[tuple[CollectionTask, RawDataAsset]]:
        if source_batch.closed_at is None:
            return []
        statement = (
            select(CollectionTask, RawDataAsset)
            .join(RawDataAsset, RawDataAsset.task_id == CollectionTask.task_id)
            .join(CollectionBatch, CollectionBatch.batch_id == CollectionTask.batch_id)
            .where(
                CollectionTask.api_name == api_name,
                CollectionTask.status.in_(
                    (
                        CollectionTaskStatus.SUCCESS.value,
                        CollectionTaskStatus.EMPTY_VALID.value,
                    )
                ),
                RawDataAsset.is_complete.is_(True),
                RawDataAsset.sealed_at <= source_batch.closed_at,
                CollectionBatch.status == BatchStatus.CLOSED.value,
            )
        )
        if scope == ReleaseScope.DATE:
            statement = statement.where(
                RawDataAsset.business_date.is_(business_date)
                if business_date is None
                else RawDataAsset.business_date == business_date
            )
        elif scope == ReleaseScope.MONTH:
            if business_date is None:
                return []
            statement = statement.where(
                func.date_trunc("month", RawDataAsset.business_date)
                == func.date_trunc("month", business_date)
            )
        rows = session.execute(
            statement.distinct(CollectionTask.scope_key).order_by(
                CollectionTask.scope_key,
                RawDataAsset.sealed_at.desc(),
                RawDataAsset.asset_id.desc(),
            )
        ).all()
        return [(collection_task, asset) for collection_task, asset in rows]

    @staticmethod
    def _upsert_dependency(
        session: Session,
        *,
        process_id: UUID,
        dependency_type: DependencyType,
        dependency_name: str,
        scope_key: str,
        scope: dict[str, object],
        status: str,
        resolved_asset_id: UUID | None,
        resolved_release_process_id: UUID | None,
        blocked_reason: str | None,
    ) -> None:
        values = {
            "process_id": process_id,
            "dependency_type": dependency_type.value,
            "dependency_name": dependency_name,
            "dependency_scope_key": scope_key,
            "dependency_scope": scope,
            "status": status,
            "resolved_asset_id": resolved_asset_id,
            "resolved_release_process_id": resolved_release_process_id,
            "blocked_reason": blocked_reason,
        }
        session.execute(
            insert(ProcessingDependency)
            .values(**values)
            .on_conflict_do_update(
                index_elements=(
                    ProcessingDependency.process_id,
                    ProcessingDependency.dependency_type,
                    ProcessingDependency.dependency_name,
                    ProcessingDependency.dependency_scope_key,
                ),
                set_=values,
            )
        )

    @staticmethod
    def _refresh_task_readiness(
        session: Session,
        task: ProcessingTask,
        *,
        now: datetime,
    ) -> ProcessingTaskStatus:
        statuses = tuple(
            session.scalars(
                select(ProcessingDependency.status).where(
                    ProcessingDependency.process_id == task.process_id
                )
            )
        )
        if statuses and all(item == DependencyStatus.READY.value for item in statuses):
            task.status = ProcessingTaskStatus.QUEUED.value
            task.queued_at = task.queued_at or now
            task.error_message = None
            return ProcessingTaskStatus.QUEUED
        unavailable = {DependencyStatus.MISSING.value, DependencyStatus.FAILED.value}
        if any(item in unavailable for item in statuses):
            task.status = ProcessingTaskStatus.BLOCKED.value
            task.error_message = "one or more required dependencies are unavailable"
            return ProcessingTaskStatus.BLOCKED
        task.status = ProcessingTaskStatus.WAITING_DEPENDENCY.value
        return ProcessingTaskStatus.WAITING_DEPENDENCY

    def _resolve_downstream_after_success(
        self,
        session: Session,
        process_id: UUID,
        now: datetime,
    ) -> None:
        dependencies = session.scalars(
            select(ProcessingDependency)
            .where(ProcessingDependency.resolved_release_process_id == process_id)
            .with_for_update()
        ).all()
        dependent_ids = set()
        for dependency in dependencies:
            dependency.status = DependencyStatus.READY.value
            dependency.blocked_reason = None
            dependent_ids.add(dependency.process_id)
        session.flush()
        for dependent_id in dependent_ids:
            task = session.get(ProcessingTask, dependent_id)
            if task is not None and task.status not in PROCESSING_TERMINAL_STATUSES:
                self._refresh_task_readiness(session, task, now=now)

    @staticmethod
    def _block_downstream_after_failure(
        session: Session,
        process_id: UUID,
        message: str,
    ) -> None:
        dependencies = session.scalars(
            select(ProcessingDependency)
            .where(ProcessingDependency.resolved_release_process_id == process_id)
            .with_for_update()
        ).all()
        for dependency in dependencies:
            dependency.status = DependencyStatus.FAILED.value
            dependency.blocked_reason = message
            session.execute(
                update(ProcessingTask)
                .where(
                    ProcessingTask.process_id == dependency.process_id,
                    ProcessingTask.status.not_in(PROCESSING_TERMINAL_STATUSES),
                )
                .values(
                    status=ProcessingTaskStatus.BLOCKED.value,
                    error_message=f"upstream processing failed: {message}",
                )
            )


def _priority_for_batch(batch_type: BatchType) -> int:
    return {
        BatchType.DAILY: 100,
        BatchType.MASTER: 100,
        BatchType.HOT: 100,
        BatchType.DELAYED: 100,
        BatchType.REPAIR: 200,
        BatchType.BACKFILL: 400,
    }[batch_type]


def _raw_dependency_names(spec: DatasetSpec) -> set[str]:
    return {
        dependency.name
        for dependency in spec.dependencies
        if dependency.kind == DependencyKind.RAW_ASSET
    }


def _affected_dataset_specs(
    dataset_specs: Sequence[DatasetSpec],
    batch_api_names: set[str],
) -> tuple[DatasetSpec, ...]:
    selected_names = {
        spec.dataset_name for spec in dataset_specs if _raw_dependency_names(spec) & batch_api_names
    }
    changed = True
    while changed:
        changed = False
        for spec in dataset_specs:
            if spec.dataset_name in selected_names:
                continue
            if any(
                dependency.kind == DependencyKind.DATASET_RELEASE
                and dependency.triggers_recompute
                and dependency.name in selected_names
                and dependency.scope == spec.release_scope
                for dependency in spec.dependencies
            ):
                selected_names.add(spec.dataset_name)
                changed = True
    return tuple(spec for spec in dataset_specs if spec.dataset_name in selected_names)


def _unknown_stock_codes(message: str | None) -> set[str]:
    if message is None or not message.startswith(UNKNOWN_STOCKS_ERROR_PREFIX):
        return set()
    try:
        value = literal_eval(message.removeprefix(UNKNOWN_STOCKS_ERROR_PREFIX).strip())
    except (SyntaxError, ValueError):
        return set()
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        return set()
    return set(value)


def _release_scope_key(scope: ReleaseScope, business_date: date | None) -> str:
    if scope == ReleaseScope.GLOBAL:
        return "GLOBAL"
    if business_date is None:
        raise ValueError(f"{scope.value} release requires a business date")
    if scope == ReleaseScope.DATE:
        return business_date.isoformat()
    if scope == ReleaseScope.MONTH:
        return business_date.strftime("%Y-%m")
    return business_date.isoformat()
