from sqlalchemy.ext.asyncio import AsyncSession


class StockRepository:
    """Database access boundary for stock data."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
