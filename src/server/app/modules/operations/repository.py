from sqlalchemy.ext.asyncio import AsyncSession


class OperationsRepository:
    """Database access boundary for operational records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
