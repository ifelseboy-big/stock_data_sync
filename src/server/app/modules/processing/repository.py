import json
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4, uuid5

import structlog
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.catalog import DatasetSpec, DependencyKind, ReleaseScope
from app.catalog.datasets import (
    ALL_DATASET_SPECS,
    STOCK_DAILY_CORE_DATASET,
    STOCK_DAILY_LIMIT_DATASET,
)
from app.common.errors import ProcessingError
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

type SessionFactory = Callable[[], Session]

PROCESSING_VERSION_NAMESPACE = UUID("24f4614f-5c65-59af-9955-bc7352d39d51")
PROCESSING_PLANNER_REVISION = 2
PROCESSING_TERMINAL_STATUSES = frozenset(
    {
        ProcessingTaskStatus.SUCCESS.value,
        ProcessingTaskStatus.FAILED.value,
        ProcessingTaskStatus.SKIPPED.value,
        ProcessingTaskStatus.CANCELLED.value,
    }
)
STOCK_DAILY_CONFLICT_DATASETS = frozenset({"stock_daily.core", "stock_daily.limit"})
DATE_SCOPED_DATASETS = frozenset(
    spec.dataset_name for spec in ALL_DATASET_SPECS if spec.release_scope == ReleaseScope.DATE
)
STOCK_DAILY_BLOCKING_CORE_STATUSES = (
    ProcessingTaskStatus.QUEUED.value,
    ProcessingTaskStatus.RUNNING.value,
    ProcessingTaskStatus.RETRY_WAIT.value,
)
STOCK_DAILY_CORE_PROCESS_TYPE = (
    f"{STOCK_DAILY_CORE_DATASET.processor}@{STOCK_DAILY_CORE_DATASET.processor_version}"
)
STOCK_DAILY_LIMIT_PROCESS_TYPE = (
    f"{STOCK_DAILY_LIMIT_DATASET.processor}@{STOCK_DAILY_LIMIT_DATASET.processor_version}"
)


class ProcessingRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def plan_closed_batches(
        self,
        dataset_specs: Sequence[DatasetSpec],
        *,
        now: datetime,
        source_batch_ids: Sequence[UUID] | None = None,
        max_batches: int = 100,
    ) -> ProcessingPlanResult:
        if max_batches < 1:
            raise ValueError("max_batches must be positive")
        if source_batch_ids is not None and not source_batch_ids:
            return ProcessingPlanResult(0, 0, 0, 0)

        plan_version = _processing_plan_version(dataset_specs)
        scanned_batch_count = 0
        created_task_count = 0
        queued_task_count = 0
        blocked_task_count = 0
        specs_by_dataset = {spec.dataset_name: spec for spec in dataset_specs}
        requested_batch_ids = (
            tuple(dict.fromkeys(source_batch_ids)) if source_batch_ids is not None else None
        )
        attempted_batch_ids: set[UUID] = set()
        iterations = len(requested_batch_ids) if requested_batch_ids is not None else max_batches
        for index in range(iterations):
            with self._session_factory() as session, session.begin():
                batch_statement = select(CollectionBatch).where(
                    CollectionBatch.status == BatchStatus.CLOSED.value
                )
                if requested_batch_ids is not None:
                    batch_statement = batch_statement.where(
                        CollectionBatch.batch_id == requested_batch_ids[index]
                    )
                else:
                    batch_statement = batch_statement.where(
                        or_(
                            CollectionBatch.processing_plan_version.is_(None),
                            CollectionBatch.processing_plan_version < plan_version,
                            CollectionBatch.processing_plan_version > plan_version,
                        )
                    )
                    if attempted_batch_ids:
                        batch_statement = batch_statement.where(
                            CollectionBatch.batch_id.not_in(attempted_batch_ids)
                        )
                batch = session.scalar(
                    batch_statement.order_by(
                        CollectionBatch.processing_plan_version.asc().nullsfirst(),
                        CollectionBatch.closed_at.desc().nullslast(),
                        CollectionBatch.batch_id,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if batch is None:
                    if requested_batch_ids is None:
                        break
                    continue
                scanned_batch_count += 1
                attempted_batch_ids.add(batch.batch_id)
                created, queued, blocked, complete = self._plan_closed_batch(
                    session,
                    batch=batch,
                    dataset_specs=dataset_specs,
                    specs_by_dataset=specs_by_dataset,
                    now=now,
                )
                created_task_count += created
                queued_task_count += queued
                blocked_task_count += blocked
                if complete and requested_batch_ids is None:
                    batch.processing_plan_version = plan_version
                    batch.processing_planned_at = now

        return ProcessingPlanResult(
            scanned_batch_count=scanned_batch_count,
            created_task_count=created_task_count,
            queued_task_count=queued_task_count,
            blocked_task_count=blocked_task_count,
        )

    def _plan_closed_batch(
        self,
        session: Session,
        *,
        batch: CollectionBatch,
        dataset_specs: Sequence[DatasetSpec],
        specs_by_dataset: dict[str, DatasetSpec],
        now: datetime,
    ) -> tuple[int, int, int, bool]:
        batch_api_names = set(
            session.scalars(
                select(CollectionTask.api_name)
                .where(CollectionTask.batch_id == batch.batch_id)
                .distinct()
            )
        )
        planned_specs: dict[UUID, DatasetSpec] = {}
        created_task_count = 0
        for spec in _affected_dataset_specs(dataset_specs, batch_api_names):
            if not (_raw_dependency_names(spec) & batch_api_names) and not (
                self._has_all_raw_dependencies(session, source_batch=batch, spec=spec)
            ):
                continue
            process_id, created = self._upsert_processing_task(
                session,
                batch=batch,
                spec=spec,
                now=now,
            )
            created_task_count += int(created)
            planned_specs[process_id] = spec

        if created_task_count and batch.business_date is not None:
            ensure_monthly_partitions(
                session.connection(),
                reference_date=batch.business_date,
                months_ahead=0,
            )
        if not planned_specs:
            return created_task_count, 0, 0, True

        replannable = (
            ProcessingTaskStatus.WAITING_DEPENDENCY.value,
            ProcessingTaskStatus.BLOCKED.value,
        )
        candidate_ids = tuple(
            session.scalars(
                select(ProcessingTask.process_id).where(
                    ProcessingTask.process_id.in_(tuple(planned_specs)),
                    ProcessingTask.status.in_(replannable),
                )
            )
        )
        tasks = tuple(
            session.scalars(
                select(ProcessingTask)
                .where(
                    ProcessingTask.process_id.in_(candidate_ids),
                    ProcessingTask.status.in_(replannable),
                )
                .order_by(ProcessingTask.process_id)
                .with_for_update(skip_locked=True)
            )
        )
        queued_task_count = 0
        blocked_task_count = 0
        for task in tasks:
            self._resolve_dependencies(
                session,
                task=task,
                spec=planned_specs[task.process_id],
                source_batch=batch,
                specs_by_dataset=specs_by_dataset,
            )
            status = self._refresh_task_readiness(session, task, now=now)
            queued_task_count += int(status == ProcessingTaskStatus.QUEUED)
            blocked_task_count += int(status == ProcessingTaskStatus.BLOCKED)
        locked_task_ids = tuple(task.process_id for task in tasks)
        waiting_dataset_dependency = bool(
            locked_task_ids
            and session.scalar(
                select(func.count())
                .select_from(ProcessingDependency)
                .where(
                    ProcessingDependency.process_id.in_(locked_task_ids),
                    ProcessingDependency.dependency_type == DependencyType.DATASET_RELEASE.value,
                    ProcessingDependency.status == DependencyStatus.WAITING.value,
                )
            )
        )
        return (
            created_task_count,
            queued_task_count,
            blocked_task_count,
            len(tasks) == len(candidate_ids) and not waiting_dataset_dependency,
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
            active_running = aliased(ProcessingTask)
            running_scope_conflict = (
                select(active_running.process_id)
                .where(
                    active_running.status == ProcessingTaskStatus.RUNNING.value,
                    active_running.output_dataset == ProcessingTask.output_dataset,
                    or_(
                        ProcessingTask.output_dataset.not_in(DATE_SCOPED_DATASETS),
                        active_running.business_date.is_not_distinct_from(
                            ProcessingTask.business_date
                        ),
                    ),
                )
                .exists()
            )
            active_core = aliased(ProcessingTask)
            active_core_dates = select(active_core.business_date).where(
                active_core.output_dataset == "stock_daily.core",
                or_(
                    active_core.status.in_(STOCK_DAILY_BLOCKING_CORE_STATUSES),
                    and_(
                        active_core.status == ProcessingTaskStatus.WAITING_DEPENDENCY.value,
                        active_core.process_type == STOCK_DAILY_CORE_PROCESS_TYPE,
                    ),
                ),
                active_core.business_date.is_not(None),
            )
            task_statement = select(ProcessingTask).where(
                or_(
                    ProcessingTask.status == ProcessingTaskStatus.QUEUED.value,
                    (ProcessingTask.status == ProcessingTaskStatus.RETRY_WAIT.value)
                    & (ProcessingTask.next_retry_at.is_not(None))
                    & (ProcessingTask.next_retry_at <= now),
                ),
                ~running_scope_conflict,
                or_(
                    ProcessingTask.output_dataset != "stock_daily.limit",
                    ProcessingTask.business_date.not_in(active_core_dates),
                ),
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
            execution_token = uuid4()
            task.execution_token = execution_token
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
                execution_token=execution_token,
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
            publication_locked = _lock_publication_scope(
                session,
                dataset_name=spec.dataset_name,
                business_date=task.business_date,
            )
            if publication_locked:
                published_at = max(published_at, datetime.now(published_at.tzinfo))
            process = session.scalar(
                select(ProcessingTask)
                .where(ProcessingTask.process_id == task.process_id)
                .with_for_update()
            )
            if process is None:
                raise RuntimeError("unknown processing task")
            if (
                process.status != ProcessingTaskStatus.RUNNING.value
                or process.execution_token != task.execution_token
            ):
                return ProcessingTransition(
                    task.process_id,
                    ProcessingTaskStatus(process.status),
                    process.next_retry_at,
                )
            expected_process_type = f"{spec.processor}@{spec.processor_version}"
            if (
                spec.dataset_name in STOCK_DAILY_CONFLICT_DATASETS
                and process.process_type != expected_process_type
            ):
                raise ProcessingError(
                    "processing task uses a stale processor version; replan the closed batch"
                )
            if spec.dataset_name == "stock_daily.limit":
                self._ensure_no_active_stock_daily_core(
                    session,
                    business_date=task.business_date,
                )
                self._refresh_stock_daily_core_dependency(
                    session,
                    process_id=task.process_id,
                    business_date=task.business_date,
                )
            if expected_process_type in {
                STOCK_DAILY_CORE_PROCESS_TYPE,
                STOCK_DAILY_LIMIT_PROCESS_TYPE,
            } and not _has_current_stock_daily_output_version(session, process, spec):
                raise ProcessingError(
                    "processing task output lineage is stale; replan the closed batch"
                )
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
            process.execution_token = None
            process.rows_read = rows_read
            process.rows_rejected = rows_rejected + publication.rows_rejected
            process.rows_written = rows_written
            process.error_message = None
            process.warning_message = "\n".join(
                (*prepared.warning_messages, *publication.warning_messages)
            )[:4000] or None
            if spec.dataset_name == "stock_daily.core":
                self._invalidate_stale_stock_daily_limit(
                    session,
                    core_process_id=process.process_id,
                    business_date=task.business_date,
                    now=published_at,
                )
            self._resolve_downstream_after_success(session, process.process_id, published_at)
            return ProcessingTransition(
                task.process_id,
                ProcessingTaskStatus.SUCCESS,
                None,
            )

    @staticmethod
    def _ensure_no_active_stock_daily_core(
        session: Session,
        *,
        business_date: date | None,
    ) -> None:
        if business_date is None:
            raise ProcessingError("stock_daily.limit requires a business date")
        active_count = session.scalar(
            select(func.count())
            .select_from(ProcessingTask)
            .where(
                ProcessingTask.output_dataset == "stock_daily.core",
                ProcessingTask.business_date == business_date,
                or_(
                    ProcessingTask.status.in_(STOCK_DAILY_BLOCKING_CORE_STATUSES),
                    and_(
                        ProcessingTask.status == ProcessingTaskStatus.WAITING_DEPENDENCY.value,
                        ProcessingTask.process_type == STOCK_DAILY_CORE_PROCESS_TYPE,
                    ),
                ),
            )
        )
        if int(active_count or 0):
            raise ProcessingError(
                "stock_daily.limit is waiting for the latest stock_daily.core release",
                retryable=True,
            )

    @staticmethod
    def _refresh_stock_daily_core_dependency(
        session: Session,
        *,
        process_id: UUID,
        business_date: date | None,
    ) -> None:
        if business_date is None:
            raise ProcessingError("stock_daily.limit requires a business date")
        dependency = session.scalar(
            select(ProcessingDependency)
            .where(
                ProcessingDependency.process_id == process_id,
                ProcessingDependency.dependency_type == DependencyType.DATASET_RELEASE.value,
                ProcessingDependency.dependency_name == "stock_daily.core",
                ProcessingDependency.dependency_scope_key == business_date.isoformat(),
            )
            .with_for_update()
        )
        if dependency is None:
            raise ProcessingError("stock_daily.limit has no stock_daily.core dependency")
        release = session.get(
            DatasetRelease,
            ("stock_daily.core", ReleaseScope.DATE.value, business_date.isoformat()),
        )
        if release is None:
            raise ProcessingError(
                "stock_daily.limit cannot publish without a current stock_daily.core release",
                retryable=True,
            )
        dependency.resolved_release_process_id = release.process_id
        dependency.status = DependencyStatus.READY.value
        dependency.blocked_reason = None

    def _invalidate_stale_stock_daily_limit(
        self,
        session: Session,
        *,
        core_process_id: UUID,
        business_date: date | None,
        now: datetime,
    ) -> None:
        if business_date is None:
            return
        scope_key = business_date.isoformat()
        observed_release = session.get(
            DatasetRelease,
            ("stock_daily.limit", ReleaseScope.DATE.value, scope_key),
        )
        if observed_release is None:
            planned_limit = self._current_stock_daily_limit_for_core(
                session,
                core_process_id=core_process_id,
                business_date=business_date,
                now=now,
            )
            if planned_limit is not None:
                self._cancel_superseded_stock_daily_limit_tasks(
                    session,
                    business_date=business_date,
                    keep_process_id=planned_limit.process_id,
                    now=now,
                )
                return
            self._replan_stock_daily_limit_without_release(
                session,
                core_process_id=core_process_id,
                business_date=business_date,
                now=now,
            )
            return
        limit_process_id = observed_release.process_id
        limit_task = session.scalar(
            select(ProcessingTask)
            .where(ProcessingTask.process_id == limit_process_id)
            .with_for_update()
        )
        dependencies = session.scalars(
            select(ProcessingDependency)
            .where(ProcessingDependency.process_id == limit_process_id)
            .order_by(
                ProcessingDependency.dependency_type,
                ProcessingDependency.dependency_name,
                ProcessingDependency.dependency_scope_key,
            )
            .with_for_update()
        ).all()
        core_dependency = next(
            (
                dependency
                for dependency in dependencies
                if dependency.dependency_type == DependencyType.DATASET_RELEASE.value
                and dependency.dependency_name == "stock_daily.core"
                and dependency.dependency_scope_key == scope_key
            ),
            None,
        )
        limit_release = session.scalar(
            select(DatasetRelease)
            .where(
                DatasetRelease.dataset_name == "stock_daily.limit",
                DatasetRelease.scope_type == ReleaseScope.DATE.value,
                DatasetRelease.scope_key == scope_key,
                DatasetRelease.process_id == limit_process_id,
            )
            .with_for_update()
        )
        if limit_release is None:
            return
        if (
            core_dependency is not None
            and core_dependency.resolved_release_process_id == core_process_id
        ):
            return

        session.delete(limit_release)
        planned_limit = self._current_stock_daily_limit_for_core(
            session,
            core_process_id=core_process_id,
            business_date=business_date,
            now=now,
        )
        if planned_limit is not None:
            structlog.get_logger("processing_repository").info(
                "stale_stock_daily_limit_release_replaced_by_planned_task",
                prior_process_id=str(limit_process_id),
                successor_process_id=str(planned_limit.process_id),
                core_process_id=str(core_process_id),
                business_date=business_date.isoformat(),
            )
            return
        if core_dependency is None or limit_task is None:
            structlog.get_logger("processing_repository").warning(
                "stale_stock_daily_limit_release_removed_without_dependency",
                process_id=str(limit_process_id),
                business_date=business_date.isoformat(),
            )
            return
        successor = self._create_stock_daily_limit_successor(
            session,
            prior_task=limit_task,
            prior_dependencies=dependencies,
            core_process_id=core_process_id,
            business_date=business_date,
            now=now,
        )
        structlog.get_logger("processing_repository").info(
            "stale_stock_daily_limit_release_replanned",
            prior_process_id=str(limit_process_id),
            successor_process_id=str(successor.process_id),
            core_process_id=str(core_process_id),
            business_date=business_date.isoformat(),
        )

    def _replan_stock_daily_limit_without_release(
        self,
        session: Session,
        *,
        core_process_id: UUID,
        business_date: date,
        now: datetime,
    ) -> None:
        active_tasks = session.scalars(
            select(ProcessingTask)
            .where(
                ProcessingTask.output_dataset == "stock_daily.limit",
                ProcessingTask.business_date == business_date,
                ProcessingTask.process_type == STOCK_DAILY_LIMIT_PROCESS_TYPE,
                ProcessingTask.status.not_in(PROCESSING_TERMINAL_STATUSES),
            )
            .order_by(ProcessingTask.process_id)
            .with_for_update()
        ).all()
        template = self._freshest_stock_daily_limit_task(session, active_tasks)
        for task in active_tasks:
            if task.status != ProcessingTaskStatus.RUNNING.value:
                _cancel_superseded_processing_task(task, now=now)
        if template is None:
            historical_tasks = session.scalars(
                select(ProcessingTask)
                .where(
                    ProcessingTask.output_dataset == "stock_daily.limit",
                    ProcessingTask.business_date == business_date,
                )
                .order_by(ProcessingTask.process_id)
                .with_for_update()
            ).all()
            template = self._freshest_stock_daily_limit_task(session, historical_tasks)
        if template is None:
            structlog.get_logger("processing_repository").warning(
                "stock_daily_limit_replan_skipped_without_template",
                core_process_id=str(core_process_id),
                business_date=business_date.isoformat(),
            )
            return
        dependencies = session.scalars(
            select(ProcessingDependency)
            .where(ProcessingDependency.process_id == template.process_id)
            .order_by(
                ProcessingDependency.dependency_type,
                ProcessingDependency.dependency_name,
                ProcessingDependency.dependency_scope_key,
            )
            .with_for_update()
        ).all()
        if not dependencies:
            structlog.get_logger("processing_repository").warning(
                "stock_daily_limit_replan_skipped_without_dependencies",
                process_id=str(template.process_id),
                core_process_id=str(core_process_id),
                business_date=business_date.isoformat(),
            )
            return
        successor = self._create_stock_daily_limit_successor(
            session,
            prior_task=template,
            prior_dependencies=dependencies,
            core_process_id=core_process_id,
            business_date=business_date,
            now=now,
        )
        structlog.get_logger("processing_repository").info(
            "stock_daily_limit_replanned_without_release",
            prior_process_id=str(template.process_id),
            successor_process_id=str(successor.process_id),
            core_process_id=str(core_process_id),
            business_date=business_date.isoformat(),
        )

    @staticmethod
    def _cancel_superseded_stock_daily_limit_tasks(
        session: Session,
        *,
        business_date: date,
        keep_process_id: UUID,
        now: datetime,
    ) -> None:
        tasks = session.scalars(
            select(ProcessingTask)
            .where(
                ProcessingTask.output_dataset == "stock_daily.limit",
                ProcessingTask.business_date == business_date,
                ProcessingTask.process_type == STOCK_DAILY_LIMIT_PROCESS_TYPE,
                ProcessingTask.process_id != keep_process_id,
                ProcessingTask.status.not_in(PROCESSING_TERMINAL_STATUSES),
            )
            .order_by(ProcessingTask.process_id)
            .with_for_update()
        ).all()
        for task in tasks:
            if task.status != ProcessingTaskStatus.RUNNING.value:
                _cancel_superseded_processing_task(task, now=now)

    def _current_stock_daily_limit_for_core(
        self,
        session: Session,
        *,
        core_process_id: UUID,
        business_date: date,
        now: datetime,
    ) -> ProcessingTask | None:
        matching_processes = select(ProcessingDependency.process_id).where(
            ProcessingDependency.dependency_type == DependencyType.DATASET_RELEASE.value,
            ProcessingDependency.dependency_name == "stock_daily.core",
            ProcessingDependency.dependency_scope_key == business_date.isoformat(),
            ProcessingDependency.resolved_release_process_id == core_process_id,
        )
        tasks = session.scalars(
            select(ProcessingTask)
            .where(
                ProcessingTask.process_id.in_(matching_processes),
                ProcessingTask.output_dataset == "stock_daily.limit",
                ProcessingTask.business_date == business_date,
                ProcessingTask.process_type == STOCK_DAILY_LIMIT_PROCESS_TYPE,
                ProcessingTask.status.not_in(PROCESSING_TERMINAL_STATUSES),
            )
            .order_by(ProcessingTask.process_id)
            .with_for_update()
        ).all()
        task = self._freshest_stock_daily_limit_task(session, tasks)
        if task is None:
            return None
        dependency = session.scalar(
            select(ProcessingDependency)
            .where(
                ProcessingDependency.process_id == task.process_id,
                ProcessingDependency.dependency_type == DependencyType.DATASET_RELEASE.value,
                ProcessingDependency.dependency_name == "stock_daily.core",
                ProcessingDependency.dependency_scope_key == business_date.isoformat(),
                ProcessingDependency.resolved_release_process_id == core_process_id,
            )
            .with_for_update()
        )
        if dependency is None:
            return None
        dependency.status = DependencyStatus.READY.value
        dependency.blocked_reason = None
        if task.status != ProcessingTaskStatus.RUNNING.value:
            self._refresh_task_readiness(session, task, now=now)
        return task

    @staticmethod
    def _freshest_stock_daily_limit_task(
        session: Session,
        tasks: Sequence[ProcessingTask],
    ) -> ProcessingTask | None:
        if not tasks:
            return None
        task_ids = tuple(task.process_id for task in tasks)
        sealed_at_by_process: dict[UUID, datetime] = {
            process_id: sealed_at
            for process_id, sealed_at in session.execute(
                select(
                    ProcessingDependency.process_id,
                    func.max(RawDataAsset.sealed_at),
                )
                .join(
                    RawDataAsset,
                    RawDataAsset.asset_id == ProcessingDependency.resolved_asset_id,
                )
                .where(
                    ProcessingDependency.process_id.in_(task_ids),
                    ProcessingDependency.dependency_type == DependencyType.RAW_ASSET.value,
                )
                .group_by(ProcessingDependency.process_id)
            )
            if sealed_at is not None
        }

        def freshness_key(task: ProcessingTask) -> tuple[bool, float, float, str]:
            sealed_at = sealed_at_by_process.get(task.process_id)
            queued_or_finished_at = task.queued_at or task.finished_at
            return (
                sealed_at is not None,
                sealed_at.timestamp() if sealed_at is not None else float("-inf"),
                (
                    queued_or_finished_at.timestamp()
                    if queued_or_finished_at is not None
                    else float("-inf")
                ),
                str(task.process_id),
            )

        return max(tasks, key=freshness_key)

    def _create_stock_daily_limit_successor(
        self,
        session: Session,
        *,
        prior_task: ProcessingTask,
        prior_dependencies: Sequence[ProcessingDependency],
        core_process_id: UUID,
        business_date: date,
        now: datetime,
    ) -> ProcessingTask:
        output_version = _stock_daily_limit_successor_output_version(
            source_batch_id=prior_task.source_batch_id,
            business_date=business_date,
            core_process_id=core_process_id,
        )
        process_id = uuid5(PROCESSING_VERSION_NAMESPACE, f"process:{output_version}")
        created_id = session.execute(
            insert(ProcessingTask)
            .values(
                process_id=process_id,
                source_batch_id=prior_task.source_batch_id,
                process_type=STOCK_DAILY_LIMIT_PROCESS_TYPE,
                business_date=business_date,
                output_dataset="stock_daily.limit",
                output_version=output_version,
                status=ProcessingTaskStatus.WAITING_DEPENDENCY.value,
                priority=prior_task.priority,
                max_attempts=max(
                    prior_task.max_attempts,
                    STOCK_DAILY_LIMIT_DATASET.max_attempts,
                ),
            )
            .on_conflict_do_nothing(index_elements=(ProcessingTask.output_version,))
            .returning(ProcessingTask.process_id)
        ).scalar_one_or_none()
        successor = session.scalar(
            select(ProcessingTask).where(ProcessingTask.process_id == process_id).with_for_update()
        )
        if successor is None:
            raise RuntimeError("failed to create stock_daily.limit successor task")
        for dependency in prior_dependencies:
            is_core_dependency = (
                dependency.dependency_type == DependencyType.DATASET_RELEASE.value
                and dependency.dependency_name == "stock_daily.core"
                and dependency.dependency_scope_key == business_date.isoformat()
            )
            self._upsert_dependency(
                session,
                process_id=successor.process_id,
                dependency_type=DependencyType(dependency.dependency_type),
                dependency_name=dependency.dependency_name,
                scope_key=dependency.dependency_scope_key,
                scope=dependency.dependency_scope,
                status=(DependencyStatus.READY.value if is_core_dependency else dependency.status),
                resolved_asset_id=dependency.resolved_asset_id,
                resolved_release_process_id=(
                    core_process_id
                    if is_core_dependency
                    else dependency.resolved_release_process_id
                ),
                blocked_reason=None if is_core_dependency else dependency.blocked_reason,
            )
        session.flush()
        if created_id is not None or successor.status in {
            ProcessingTaskStatus.WAITING_DEPENDENCY.value,
            ProcessingTaskStatus.BLOCKED.value,
        }:
            self._refresh_task_readiness(session, successor, now=now)
        return successor

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
            if (
                process.status != ProcessingTaskStatus.RUNNING.value
                or process.execution_token != task.execution_token
            ):
                return ProcessingTransition(
                    task.process_id,
                    ProcessingTaskStatus(process.status),
                    process.next_retry_at,
                )
            retry_at = None
            if retryable and process.attempt_count < process.max_attempts:
                retry_at = now + timedelta(seconds=min(30 * 2 ** (process.attempt_count - 1), 900))
                status = ProcessingTaskStatus.RETRY_WAIT
            else:
                status = ProcessingTaskStatus.FAILED
            process.status = status.value
            process.next_retry_at = retry_at
            process.execution_token = None
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
            tasks = session.scalars(
                statement.order_by(ProcessingTask.process_id).with_for_update(skip_locked=True)
            ).all()
            for task in tasks:
                task.status = ProcessingTaskStatus.RETRY_WAIT.value
                task.attempt_count = max(task.attempt_count - 1, 0)
                task.next_retry_at = now
                task.execution_token = None
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
        now: datetime,
    ) -> tuple[UUID, bool]:
        process_id, output_version = _processing_identity(batch, spec)
        current = session.get(ProcessingTask, process_id)
        if current is not None:
            return process_id, False
        prior_tasks = tuple(
            session.scalars(
                select(ProcessingTask).where(
                    ProcessingTask.source_batch_id == batch.batch_id,
                    ProcessingTask.output_dataset == spec.dataset_name,
                    ProcessingTask.process_id != process_id,
                )
            )
        )
        if prior_tasks:
            latest = max(prior_tasks, key=_processing_task_generation_key)
            expected_process_type = f"{spec.processor}@{spec.processor_version}"
            for prior_task in prior_tasks:
                if (
                    prior_task.process_type != expected_process_type
                    and prior_task.status not in PROCESSING_TERMINAL_STATUSES
                    and prior_task.status != ProcessingTaskStatus.RUNNING.value
                ):
                    _cancel_replaced_processor_task(
                        prior_task,
                        expected_process_type=expected_process_type,
                        now=now,
                    )
            if latest.status == ProcessingTaskStatus.SUCCESS.value or (
                latest.process_type == expected_process_type
                and latest.status not in PROCESSING_TERMINAL_STATUSES
            ):
                return latest.process_id, False
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
        specs_by_dataset: dict[str, DatasetSpec],
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
                upstream_spec = specs_by_dataset.get(dependency.name)
                upstream = None
                if upstream_spec is not None:
                    expected_process_id, _output_version = _processing_identity(
                        source_batch,
                        upstream_spec,
                    )
                    upstream = session.get(ProcessingTask, expected_process_id)
                if upstream is None:
                    upstream_candidates = tuple(
                        session.scalars(
                            select(ProcessingTask).where(
                                ProcessingTask.source_batch_id == task.source_batch_id,
                                ProcessingTask.output_dataset == dependency.name,
                            )
                        )
                    )
                    if upstream_candidates:
                        upstream = max(
                            upstream_candidates,
                            key=_processing_task_generation_key,
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
            task.execution_token = None
            task.queued_at = task.queued_at or now
            task.error_message = None
            return ProcessingTaskStatus.QUEUED
        unavailable = {DependencyStatus.MISSING.value, DependencyStatus.FAILED.value}
        if any(item in unavailable for item in statuses):
            task.status = ProcessingTaskStatus.BLOCKED.value
            task.execution_token = None
            task.error_message = "one or more required dependencies are unavailable"
            return ProcessingTaskStatus.BLOCKED
        task.status = ProcessingTaskStatus.WAITING_DEPENDENCY.value
        task.execution_token = None
        return ProcessingTaskStatus.WAITING_DEPENDENCY

    def _resolve_downstream_after_success(
        self,
        session: Session,
        process_id: UUID,
        now: datetime,
    ) -> None:
        dependent_ids = tuple(
            session.scalars(
                select(ProcessingDependency.process_id)
                .where(ProcessingDependency.resolved_release_process_id == process_id)
                .distinct()
                .order_by(ProcessingDependency.process_id)
            )
        )
        if not dependent_ids:
            return
        tasks = tuple(
            session.scalars(
                select(ProcessingTask)
                .where(ProcessingTask.process_id.in_(dependent_ids))
                .order_by(ProcessingTask.process_id)
                .with_for_update()
            )
        )
        dependencies = tuple(
            session.scalars(
                select(ProcessingDependency)
                .where(
                    ProcessingDependency.resolved_release_process_id == process_id,
                    ProcessingDependency.process_id.in_(dependent_ids),
                )
                .order_by(
                    ProcessingDependency.process_id,
                    ProcessingDependency.dependency_type,
                    ProcessingDependency.dependency_name,
                    ProcessingDependency.dependency_scope_key,
                )
                .with_for_update()
            )
        )
        for dependency in dependencies:
            dependency.status = DependencyStatus.READY.value
            dependency.blocked_reason = None
        session.flush()
        for task in tasks:
            if task.status not in PROCESSING_TERMINAL_STATUSES and (
                task.status != ProcessingTaskStatus.RUNNING.value
            ):
                self._refresh_task_readiness(session, task, now=now)

    @staticmethod
    def _block_downstream_after_failure(
        session: Session,
        process_id: UUID,
        message: str,
    ) -> None:
        dependent_ids = tuple(
            session.scalars(
                select(ProcessingDependency.process_id)
                .where(ProcessingDependency.resolved_release_process_id == process_id)
                .distinct()
                .order_by(ProcessingDependency.process_id)
            )
        )
        if not dependent_ids:
            return
        tasks = tuple(
            session.scalars(
                select(ProcessingTask)
                .where(ProcessingTask.process_id.in_(dependent_ids))
                .order_by(ProcessingTask.process_id)
                .with_for_update()
            )
        )
        dependencies = tuple(
            session.scalars(
                select(ProcessingDependency)
                .where(
                    ProcessingDependency.resolved_release_process_id == process_id,
                    ProcessingDependency.process_id.in_(dependent_ids),
                )
                .order_by(
                    ProcessingDependency.process_id,
                    ProcessingDependency.dependency_type,
                    ProcessingDependency.dependency_name,
                    ProcessingDependency.dependency_scope_key,
                )
                .with_for_update()
            )
        )
        for dependency in dependencies:
            dependency.status = DependencyStatus.FAILED.value
            dependency.blocked_reason = message
        for task in tasks:
            if task.status not in PROCESSING_TERMINAL_STATUSES:
                task.status = ProcessingTaskStatus.BLOCKED.value
                task.execution_token = None
                task.error_message = f"upstream processing failed: {message}"


def _lock_publication_scope(
    session: Session,
    *,
    dataset_name: str,
    business_date: date | None,
) -> bool:
    if dataset_name not in STOCK_DAILY_CONFLICT_DATASETS or business_date is None:
        return False
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"stock_daily:{business_date.isoformat()}"},
    )
    return True


def _processing_plan_version(dataset_specs: Sequence[DatasetSpec]) -> str:
    payload = {
        "planner_revision": PROCESSING_PLANNER_REVISION,
        "datasets": [
            {
                "dataset_name": spec.dataset_name,
                "processor": spec.processor,
                "processor_version": spec.processor_version,
                "dependencies": [
                    {
                        "kind": dependency.kind.value,
                        "name": dependency.name,
                        "scope": dependency.scope.value,
                        "triggers_recompute": dependency.triggers_recompute,
                        "merge_previous_scopes": dependency.merge_previous_scopes,
                    }
                    for dependency in spec.dependencies
                ],
                "write_strategy": spec.write_strategy.value,
                "release_scope": spec.release_scope.value,
                "quality_rules": [
                    {
                        "name": rule.name,
                        "parameters": dict(rule.parameters),
                    }
                    for rule in spec.quality_rules
                ],
                "max_attempts": spec.max_attempts,
            }
            for spec in sorted(dataset_specs, key=lambda item: item.dataset_name)
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return sha256(encoded).hexdigest()


def _processing_identity(batch: CollectionBatch, spec: DatasetSpec) -> tuple[UUID, UUID]:
    output_version = _processing_output_version(
        source_batch_id=batch.batch_id,
        dataset_name=spec.dataset_name,
        processor_version=spec.processor_version,
        business_date=batch.business_date,
    )
    return (
        uuid5(PROCESSING_VERSION_NAMESPACE, f"process:{output_version}"),
        output_version,
    )


def _processing_output_version(
    *,
    source_batch_id: UUID,
    dataset_name: str,
    processor_version: str,
    business_date: date | None,
) -> UUID:
    return uuid5(
        PROCESSING_VERSION_NAMESPACE,
        f"{source_batch_id}:{dataset_name}:{processor_version}:"
        f"{business_date.isoformat() if business_date else 'GLOBAL'}",
    )


def _stock_daily_limit_successor_output_version(
    *,
    source_batch_id: UUID,
    business_date: date,
    core_process_id: UUID,
) -> UUID:
    return uuid5(
        PROCESSING_VERSION_NAMESPACE,
        f"{source_batch_id}:stock_daily.limit:"
        f"{STOCK_DAILY_LIMIT_DATASET.processor_version}:{business_date.isoformat()}:"
        f"core:{core_process_id}",
    )


def _has_current_stock_daily_output_version(
    session: Session,
    task: ProcessingTask,
    spec: DatasetSpec,
) -> bool:
    standard_version = _processing_output_version(
        source_batch_id=task.source_batch_id,
        dataset_name=spec.dataset_name,
        processor_version=spec.processor_version,
        business_date=task.business_date,
    )
    if task.output_version == standard_version:
        return True
    if spec.dataset_name != "stock_daily.limit" or task.business_date is None:
        return False
    core_process_id = session.scalar(
        select(ProcessingDependency.resolved_release_process_id).where(
            ProcessingDependency.process_id == task.process_id,
            ProcessingDependency.dependency_type == DependencyType.DATASET_RELEASE.value,
            ProcessingDependency.dependency_name == "stock_daily.core",
            ProcessingDependency.dependency_scope_key == task.business_date.isoformat(),
        )
    )
    if core_process_id is None:
        return False
    successor_version = _stock_daily_limit_successor_output_version(
        source_batch_id=task.source_batch_id,
        business_date=task.business_date,
        core_process_id=core_process_id,
    )
    return task.output_version == successor_version


def _processing_task_generation_key(task: ProcessingTask) -> tuple[str, int, str, str]:
    processor, separator, version = task.process_type.rpartition("@")
    if not separator:
        return task.process_type, -1, "", str(task.process_id)
    try:
        numeric_version = int(version)
    except ValueError:
        numeric_version = -1
    return processor, numeric_version, version, str(task.process_id)


def _cancel_superseded_processing_task(task: ProcessingTask, *, now: datetime) -> None:
    task.status = ProcessingTaskStatus.CANCELLED.value
    task.next_retry_at = None
    task.execution_token = None
    task.finished_at = now
    task.error_message = "superseded by a newer stock_daily.core publication"


def _cancel_replaced_processor_task(
    task: ProcessingTask,
    *,
    expected_process_type: str,
    now: datetime,
) -> None:
    task.status = ProcessingTaskStatus.CANCELLED.value
    task.next_retry_at = None
    task.execution_token = None
    task.finished_at = now
    task.error_message = f"replaced by current processor {expected_process_type}"


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
