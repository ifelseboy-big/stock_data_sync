from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Event, Lock
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.modules.acquisition.capacity import CapacityLevel, CapacitySnapshot
from app.modules.acquisition.domain import ClaimedCollectionTask, TaskTransition
from app.modules.acquisition.models import BatchType, CollectionTaskStatus
from app.modules.acquisition.runtime import AcquisitionRuntime
from app.modules.processing.domain import (
    ClaimedProcessingTask,
    ProcessingTransition,
)
from app.modules.processing.models import ProcessingTaskStatus
from app.modules.processing.runtime import ProcessingRuntime


class AcquisitionRepositoryStub:
    def __init__(self, task_count: int) -> None:
        self._lock = Lock()
        self.tasks = deque(_collection_task() for _ in range(task_count))
        self.claimed_ids: list[object] = []

    def claim_next(self, **_: object) -> ClaimedCollectionTask | None:
        with self._lock:
            if not self.tasks:
                return None
            task = self.tasks.popleft()
            self.claimed_ids.append(task.task_id)
            return task


class CapacityGateStub:
    def snapshot(self) -> CapacitySnapshot:
        return CapacitySnapshot(
            level=CapacityLevel.NORMAL,
            total_bytes=1_000,
            used_bytes=100,
            free_bytes=900,
            used_percent=10,
        )


class BlockingAcquisitionExecutor:
    def __init__(self, expected_count: int, initial_workers: int) -> None:
        self._lock = Lock()
        self._expected_count = expected_count
        self._initial_workers = initial_workers
        self.active = 0
        self.max_active = 0
        self.completed = 0
        self.initial_workers_started = Event()
        self.release_initial_workers = Event()
        self.all_completed = Event()

    def execute(self, task: ClaimedCollectionTask) -> TaskTransition:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == self._initial_workers:
                self.initial_workers_started.set()
        assert self.release_initial_workers.wait(timeout=3)
        with self._lock:
            self.active -= 1
            self.completed += 1
            if self.completed == self._expected_count:
                self.all_completed.set()
        return TaskTransition(task.task_id, CollectionTaskStatus.SUCCESS, None)


class ProcessingRepositoryStub:
    def __init__(self, task_count: int) -> None:
        self._lock = Lock()
        self.tasks = deque(_processing_task(index) for index in range(task_count))
        self.claimed_ids: list[object] = []

    def claim_next(self, **_: object) -> ClaimedProcessingTask | None:
        with self._lock:
            if not self.tasks:
                return None
            task = self.tasks.popleft()
            self.claimed_ids.append(task.process_id)
            return task


class BlockingProcessingExecutor:
    def __init__(self, expected_count: int, initial_workers: int) -> None:
        self._lock = Lock()
        self._expected_count = expected_count
        self._initial_workers = initial_workers
        self.active = 0
        self.max_active = 0
        self.completed = 0
        self.initial_workers_started = Event()
        self.release_initial_workers = Event()
        self.all_completed = Event()

    def execute(self, task: ClaimedProcessingTask) -> ProcessingTransition:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == self._initial_workers:
                self.initial_workers_started.set()
        assert self.release_initial_workers.wait(timeout=3)
        with self._lock:
            self.active -= 1
            self.completed += 1
            if self.completed == self._expected_count:
                self.all_completed.set()
        return ProcessingTransition(task.process_id, ProcessingTaskStatus.SUCCESS, None)


def test_acquisition_runtime_serializes_concurrent_wakeups_and_refills_workers() -> None:
    repository = AcquisitionRepositoryStub(task_count=8)
    executor = BlockingAcquisitionExecutor(expected_count=8, initial_workers=4)
    runtime = AcquisitionRuntime(
        repository=repository,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        capacity_gate=CapacityGateStub(),  # type: ignore[arg-type]
        max_workers=4,
        timezone=ZoneInfo("UTC"),
    )
    try:
        with ThreadPoolExecutor(max_workers=8) as callers:
            wakeups = tuple(
                callers.submit(runtime.dispatch, now=datetime.now(UTC)) for _ in range(8)
            )
        assert sum(future.result() for future in wakeups) == 4
        assert executor.initial_workers_started.wait(timeout=3)
        executor.release_initial_workers.set()
        assert executor.all_completed.wait(timeout=3)
    finally:
        runtime.shutdown()

    assert executor.max_active == 4
    assert len(repository.claimed_ids) == 8
    assert len(set(repository.claimed_ids)) == 8


def test_processing_runtime_serializes_concurrent_wakeups_and_refills_three_slots() -> None:
    repository = ProcessingRepositoryStub(task_count=9)
    executor = BlockingProcessingExecutor(expected_count=9, initial_workers=3)
    runtime = ProcessingRuntime(
        repository=repository,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        advisory_lock_id=123,
        max_workers=3,
        timezone=ZoneInfo("UTC"),
    )
    try:
        with ThreadPoolExecutor(max_workers=8) as callers:
            wakeups = tuple(
                callers.submit(runtime.wake, now=datetime.now(UTC)) for _ in range(8)
            )
        assert sum(future.result() for future in wakeups) == 3
        assert executor.initial_workers_started.wait(timeout=3)
        executor.release_initial_workers.set()
        assert executor.all_completed.wait(timeout=3)
    finally:
        runtime.shutdown()

    assert executor.max_active == 3
    assert len(repository.claimed_ids) == 9
    assert len(set(repository.claimed_ids)) == 9


def _collection_task() -> ClaimedCollectionTask:
    return ClaimedCollectionTask(
        task_id=uuid4(),
        batch_id=uuid4(),
        batch_type=BatchType.BACKFILL,
        business_date=date(2026, 7, 17),
        provider="TUSHARE",
        api_name="daily",
        scope_key="trade_date=20260717",
        request_params={"trade_date": "20260717"},
        attempt_count=1,
        max_attempts=3,
    )


def _processing_task(index: int) -> ClaimedProcessingTask:
    return ClaimedProcessingTask(
        process_id=uuid4(),
        source_batch_id=uuid4(),
        process_type="test@1",
        business_date=date(2026, 7, 17),
        output_dataset=f"dataset_{index}",
        output_version=uuid4(),
        attempt_count=1,
        max_attempts=3,
    )
