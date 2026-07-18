import structlog
from sqlalchemy import text

from app.db.sync_session import sync_session_scope


def dispatch_due_tasks() -> None:
    """Scheduler heartbeat; task definition scanning is added with the tasking module."""

    with sync_session_scope() as session:
        session.execute(text("SELECT 1"))
    structlog.get_logger("scheduler").debug("scheduler_poll_completed")
