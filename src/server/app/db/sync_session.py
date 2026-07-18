from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

sync_engine = create_engine(settings.database_url, pool_pre_ping=True)
SyncSessionFactory = sessionmaker(bind=sync_engine, expire_on_commit=False, autoflush=False)


@contextmanager
def sync_session_scope() -> Iterator[Session]:
    """Transaction boundary for scheduler jobs and synchronous provider code."""

    session = SyncSessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
