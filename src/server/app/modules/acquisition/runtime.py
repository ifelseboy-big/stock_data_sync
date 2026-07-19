from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo

import structlog

from app.modules.acquisition.capacity import RawStorageCapacityGate
from app.modules.acquisition.domain import TaskTransition
from app.modules.acquisition.executor import CollectionExecutor
from app.modules.acquisition.repository import AcquisitionRepository


class AcquisitionRuntime:
    def __init__(
        self,
        *,
        repository: AcquisitionRepository,
        executor: CollectionExecutor,
        capacity_gate: RawStorageCapacityGate,
        max_workers: int,
        timezone: ZoneInfo,
    ) -> None:
        self._repository = repository
        self._collection_executor = executor
        self._capacity_gate = capacity_gate
        self._max_workers = max_workers
        self._timezone = timezone
        self._thread_pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="collection",
        )
        self._futures: dict[Future[TaskTransition], object] = {}
        self._lock = Lock()
        self._dispatch_lock = Lock()
        self._stopping = False

    def dispatch(self, *, now: datetime) -> int:
        if not self._dispatch_lock.acquire(blocking=False):
            return 0
        try:
            return self._fill_available_slots(now=now)
        finally:
            self._dispatch_lock.release()

    def _fill_available_slots(self, *, now: datetime) -> int:
        capacity = self._capacity_gate.snapshot()
        submitted = 0
        while True:
            with self._lock:
                if self._stopping:
                    break
                available_slots = self._max_workers - len(self._futures)
            if available_slots <= 0:
                break

            task = self._repository.claim_next(
                allowed_batch_types=capacity.allowed_batch_types(),
                now=now,
            )
            if task is None:
                break
            future = self._thread_pool.submit(self._collection_executor.execute, task)
            with self._lock:
                self._futures[future] = task.task_id
            future.add_done_callback(self._task_finished)
            submitted += 1

        structlog.get_logger("acquisition_runtime").debug(
            "collection_dispatch_completed",
            submitted=submitted,
            capacity_level=capacity.level.value,
            free_bytes=capacity.free_bytes,
        )
        return submitted

    def inflight_count(self) -> int:
        with self._lock:
            return len(self._futures)

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
        with self._dispatch_lock:
            pass
        self._thread_pool.shutdown(wait=True, cancel_futures=False)

    def _task_finished(self, future: Future[TaskTransition]) -> None:
        with self._lock:
            task_id = self._futures.pop(future, None)
        try:
            transition = future.result()
            structlog.get_logger("acquisition_runtime").info(
                "collection_task_finished",
                task_id=str(task_id),
                status=transition.status.value,
                next_retry_at=transition.next_retry_at,
            )
        except Exception:
            structlog.get_logger("acquisition_runtime").exception(
                "collection_worker_crashed",
                task_id=str(task_id),
            )
        with self._lock:
            stopping = self._stopping
        if not stopping:
            self.dispatch(now=datetime.now(self._timezone))
