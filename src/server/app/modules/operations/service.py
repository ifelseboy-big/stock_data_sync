from app.modules.operations.repository import OperationsRepository


class OperationsService:
    """Builds operational summaries and diagnostic views."""

    def __init__(self, repository: OperationsRepository) -> None:
        self.repository = repository
