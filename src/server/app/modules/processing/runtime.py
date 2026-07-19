from datetime import datetime

import structlog

from app.modules.processing.domain import ProcessingTransition
from app.modules.processing.executor import ProcessingExecutor
from app.modules.processing.repository import ProcessingRepository


class ProcessingRuntime:
    def __init__(
        self,
        *,
        repository: ProcessingRepository,
        executor: ProcessingExecutor,
        advisory_lock_id: int,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._advisory_lock_id = advisory_lock_id

    def dispatch(self, *, now: datetime) -> ProcessingTransition | None:
        task = self._repository.claim_next(
            now=now,
            advisory_lock_id=self._advisory_lock_id,
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
