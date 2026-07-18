from sqlalchemy.ext.asyncio import AsyncSession


class TaskRepository:
    """Database access boundary for task definitions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
