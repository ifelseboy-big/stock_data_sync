from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from app.modules.operations.models import ProviderRequestLog

type SessionFactory = Callable[[], Session]

_collection_task_id: ContextVar[UUID | None] = ContextVar(
    "provider_collection_task_id",
    default=None,
)


@dataclass(frozen=True, slots=True)
class ProviderCallObservation:
    provider: str
    endpoint: str
    requested_at: datetime
    finished_at: datetime
    status: str
    duration_ms: int
    rate_limit_wait_ms: int
    row_count: int | None
    error_code: str | None


class ProviderCallRecorder(Protocol):
    def record(self, observation: ProviderCallObservation) -> None: ...


class NullProviderCallRecorder:
    def record(self, observation: ProviderCallObservation) -> None:
        del observation


class PostgresProviderCallRecorder:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def record(self, observation: ProviderCallObservation) -> None:
        task_id = _collection_task_id.get()
        if task_id is None:
            return
        try:
            with self._session_factory() as session, session.begin():
                session.add(
                    ProviderRequestLog(
                        task_id=task_id,
                        provider=observation.provider,
                        endpoint=observation.endpoint,
                        requested_at=observation.requested_at,
                        finished_at=observation.finished_at,
                        status=observation.status,
                        duration_ms=observation.duration_ms,
                        rate_limit_wait_ms=observation.rate_limit_wait_ms,
                        row_count=observation.row_count,
                        error_code=observation.error_code,
                    )
                )
        except Exception:
            structlog.get_logger("provider_call_recorder").exception(
                "provider_call_observation_persist_failed",
                task_id=str(task_id),
                provider=observation.provider,
                endpoint=observation.endpoint,
            )


@contextmanager
def collection_task_context(task_id: UUID) -> Iterator[None]:
    token = _collection_task_id.set(task_id)
    try:
        yield
    finally:
        _collection_task_id.reset(token)
