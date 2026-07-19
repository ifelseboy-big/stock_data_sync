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
                3,
                0,
                1,
                0,
            ]
        )

    async def scalar(self, _statement: object) -> object:
        self.statements.append(str(_statement))
        return next(self._values)


@pytest.mark.asyncio
async def test_system_resources_separates_database_buffers_from_api_memory(
    tmp_path: Path,
) -> None:
    config = settings.model_copy(update={"raw_data_dir": tmp_path})

    session = SystemSessionStub()
    resources = await get_system_resources(session, config)  # type: ignore[arg-type]

    assert resources.database.shared_buffers_bytes == 2 * 1024**3
    assert resources.process.memory_high_water_bytes > 0
    assert any("current_setting('shared_buffers')" in statement for statement in session.statements)
