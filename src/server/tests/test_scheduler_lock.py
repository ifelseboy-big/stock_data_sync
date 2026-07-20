from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import app.scheduler.lock as lock_module


class ConnectionStub:
    def __init__(self) -> None:
        self.events: list[str] = []

    def scalar(self, _statement: object, _params: object) -> bool:
        self.events.append("lock")
        return True

    def execute(self, _statement: object, _params: object) -> None:
        self.events.append("unlock")

    def commit(self) -> None:
        self.events.append("commit")


class EngineStub:
    def __init__(self, connection: ConnectionStub) -> None:
        self.connection_stub = connection

    @contextmanager
    def connect(self) -> Iterator[ConnectionStub]:
        yield self.connection_stub


def test_scheduler_lock_ends_transactions_but_keeps_session_open(monkeypatch: Any) -> None:
    connection = ConnectionStub()
    monkeypatch.setattr(lock_module, "sync_engine", EngineStub(connection))

    with lock_module.scheduler_singleton_lock():
        connection.events.append("yield")

    assert connection.events == ["lock", "commit", "yield", "unlock", "commit"]
