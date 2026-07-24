from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from threading import Event, Lock, Thread
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog

from app.modules.processing.domain import ClaimedProcessingTask, ProcessingTransition
from app.modules.processing.executor import ProcessingExecutor
from app.modules.processing.repository import ProcessingRepository


class ProcessingRuntime:
    def __init__(
        self,
        *,
        repository: ProcessingRepository,
        executor: ProcessingExecutor,
        advisory_lock_id: int,
        max_workers: int,
        timezone: ZoneInfo,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._advisory_lock_id = advisory_lock_id
        self._max_workers = max_workers
        self._timezone = timezone
        self._thread_pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="processing",
        )
        self._futures: dict[Future[ProcessingTransition], ClaimedProcessingTask] = {}
        self._lock = Lock()
        self._dispatch_lock = Lock()
        self._stopping = False
        self._refill_event = Event()
        self._refill_thread = Thread(
            target=self._refill_loop,
            name="processing-refill",
            daemon=True,
        )
        self._refill_thread.start()

    def dispatch(
        self,
        *,
        now: datetime,
        source_batch_ids: Sequence[UUID] | None = None,
    ) -> ProcessingTransition | None:
        task = self._repository.claim_next(
            now=now,
            advisory_lock_id=self._advisory_lock_id,
            max_running_tasks=1,
            source_batch_ids=source_batch_ids,
        )
        if task is None:
            return None
        transition = self._executor.execute(task)
        structlog.get_logger("processing_runtime").info(
            "processing_task_finished",
            process_id=str(task.process_id),
            dataset=task.output_dataset,
            status=transition.status.value,
            next_retry_at=transition.next_retry_at,
        )
        return transition

    def wake(self, *, now: datetime) -> int:
        """Fill available worker slots; completed workers keep refilling the queue."""
        return self._wake(now=now, blocking=False)

    def _wake(self, *, now: datetime, blocking: bool) -> int:
        if not self._dispatch_lock.acquire(blocking=blocking):
            return 0
        try:
            submitted = 0
            with self._lock:
                available_slots = (
                    0 if self._stopping else self._max_workers - len(self._futures)
                )
            for _ in range(available_slots):
                with self._lock:
                    if self._stopping:
                        break
                task = self._repository.claim_next(
                    now=now,
                    advisory_lock_id=self._advisory_lock_id,
                    max_running_tasks=self._max_workers,
                )
                if task is None:
                    break
                future = self._thread_pool.submit(self._executor.execute, task)
                with self._lock:
                    self._futures[future] = task
                future.add_done_callback(self._task_finished)
                submitted += 1
            structlog.get_logger("processing_runtime").debug(
                "processing_dispatch_completed",
                submitted=submitted,
                max_workers=self._max_workers,
            )
            return submitted
        finally:
            self._dispatch_lock.release()

    def inflight_count(self) -> int:
        with self._lock:
            return len(self._futures)

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
        self._refill_event.set()
        self._refill_thread.join()
        with self._dispatch_lock:
            pass
        self._thread_pool.shutdown(wait=True, cancel_futures=False)

    def _task_finished(self, future: Future[ProcessingTransition]) -> None:
        with self._lock:
            task = self._futures.pop(future, None)
        try:
            transition = future.result()
            structlog.get_logger("processing_runtime").info(
                "processing_task_finished",
                process_id=str(transition.process_id),
                dataset=task.output_dataset if task is not None else None,
                status=transition.status.value,
                next_retry_at=transition.next_retry_at,
            )
        except Exception:
            structlog.get_logger("processing_runtime").exception(
                "processing_worker_crashed",
                process_id=str(task.process_id) if task is not None else None,
                dataset=task.output_dataset if task is not None else None,
            )
        with self._lock:
            stopping = self._stopping
        if not stopping:
            self._refill_event.set()

    def _refill_loop(self) -> None:
        while True:
            self._refill_event.wait()
            self._refill_event.clear()
            with self._lock:
                if self._stopping:
                    return
            self._wake(
                now=datetime.now(self._timezone),
                blocking=True,
            )
