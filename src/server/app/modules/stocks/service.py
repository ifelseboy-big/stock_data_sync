from app.modules.stocks.repository import StockRepository


class StockService:
    """Coordinates stock synchronization use cases."""

    def __init__(self, repository: StockRepository) -> None:
        self.repository = repository
