from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.config import settings
from app.modules.system.service import get_system_resources


class SystemSessionStub:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self._values: Iterator[object] = iter(
            [
                "PostgreSQL test",
                1024,
                2 * 1024**3,
                0,
                1,
                0,
            ]
        )

    async def scalar(self, _statement: object) -> object:
        self.statements.append(str(_statement))
        return next(self._values)

    async def execute(self, statement: object) -> "MappingResultStub":
        self.statements.append(str(statement))
        return MappingResultStub()


class MappingResultStub:
    def mappings(self) -> "MappingResultStub":
        return self

    def one(self) -> dict[str, int]:
        return {
            "client_connection_count": 12,
            "active_connection_count": 2,
            "idle_connection_count": 8,
            "idle_in_transaction_connection_count": 2,
            "background_process_count": 9,
        }


@pytest.mark.asyncio
async def test_system_resources_separates_database_buffers_from_api_memory(
    tmp_path: Path,
) -> None:
    config = settings.model_copy(update={"raw_data_dir": tmp_path})

    session = SystemSessionStub()
    resources = await get_system_resources(session, config)  # type: ignore[arg-type]

    assert resources.database.shared_buffers_bytes == 2 * 1024**3
    assert resources.database.client_connection_count == 12
    assert resources.database.active_connection_count == 2
    assert resources.database.idle_connection_count == 8
    assert resources.database.idle_in_transaction_connection_count == 2
    assert resources.database.background_process_count == 9
    assert resources.process.memory_high_water_bytes > 0
    assert any("current_setting('shared_buffers')" in statement for statement in session.statements)
    assert any("backend_type = 'client backend'" in statement for statement in session.statements)
