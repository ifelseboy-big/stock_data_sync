from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.storage import RawAssetStore


@dataclass(frozen=True, slots=True)
class PreparedDataset:
    payload: object
    rows_read: int
    rows_rejected: int = 0


@dataclass(frozen=True, slots=True)
class PublicationResult:
    rows_written: int
    rows_rejected: int = 0


class DatasetProcessor(Protocol):
    name: str

    def prepare(
        self,
        task: ClaimedProcessingTask,
        dependencies: tuple[RawDependencyAsset, ...],
        asset_store: RawAssetStore,
    ) -> PreparedDataset: ...

    def write(
        self,
        session: Session,
        prepared: PreparedDataset,
        *,
        published_at: datetime,
    ) -> PublicationResult: ...
