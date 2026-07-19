from datetime import datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.catalog import DatasetSpec, SpecRegistry
from app.common.errors import ProcessingError
from app.modules.processing.domain import ClaimedProcessingTask, ProcessingTransition
from app.modules.processing.processors.base import DatasetProcessor
from app.modules.processing.repository import ProcessingRepository
from app.storage import RawAssetStore


class ProcessingExecutor:
    def __init__(
        self,
        *,
        repository: ProcessingRepository,
        dataset_specs: SpecRegistry[DatasetSpec],
        processors: dict[str, DatasetProcessor],
        asset_store: RawAssetStore,
        timezone: ZoneInfo,
    ) -> None:
        self._repository = repository
        self._dataset_specs = dataset_specs
        self._processors = processors
        self._asset_store = asset_store
        self._timezone = timezone

    def execute(self, task: ClaimedProcessingTask) -> ProcessingTransition:
        try:
            spec = self._dataset_specs.get(task.output_dataset)
            processor = self._processors[spec.processor]
            dependencies = self._repository.raw_dependencies(task.process_id)
            prepared = processor.prepare(task, dependencies, self._asset_store)
            return self._repository.publish_success(
                task,
                spec,
                prepared=prepared,
                processor=processor,
                published_at=datetime.now(self._timezone),
                rows_read=prepared.rows_read,
                rows_rejected=prepared.rows_rejected,
            )
        except ProcessingError as exc:
            return self._fail(task, str(exc), retryable=exc.retryable)
        except SQLAlchemyError as exc:
            return self._fail(task, str(exc), retryable=True)
        except (KeyError, ValueError, TypeError) as exc:
            return self._fail(task, str(exc), retryable=False)
        except Exception as exc:
            structlog.get_logger("processing_executor").exception(
                "processing_task_crashed",
                process_id=str(task.process_id),
                dataset=task.output_dataset,
            )
            return self._fail(task, str(exc), retryable=False)

    def _fail(
        self,
        task: ClaimedProcessingTask,
        message: str,
        *,
        retryable: bool,
    ) -> ProcessingTransition:
        return self._repository.fail_task(
            task,
            message=message[:4000],
            retryable=retryable,
            now=datetime.now(self._timezone),
        )
