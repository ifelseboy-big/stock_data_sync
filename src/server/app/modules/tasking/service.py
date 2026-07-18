from app.modules.tasking.repository import TaskRepository


class TaskService:
    """Coordinates task lifecycle and scheduling rules."""

    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository
