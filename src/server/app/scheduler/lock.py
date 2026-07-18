from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from app.core.config import settings
from app.db.sync_session import sync_engine


@contextmanager
def scheduler_singleton_lock() -> Iterator[None]:
    """Hold a PostgreSQL session advisory lock for the scheduler lifetime."""

    with sync_engine.connect() as connection:
        acquired = connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": settings.scheduler_advisory_lock_id},
        )
        if not acquired:
            raise RuntimeError("Another scheduler instance already holds the PostgreSQL lock")

        try:
            yield
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": settings.scheduler_advisory_lock_id},
            )
