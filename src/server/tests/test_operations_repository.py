from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.operations.repository import OperationsRepository


class _EmptyResult:
    def mappings(self) -> tuple[Any, ...]:
        return ()


class _StatementCapture:
    count_statement: Any = None
    page_statement: Any = None

    async def scalar(self, statement: Any) -> int:
        self.count_statement = statement
        return 0

    async def execute(self, statement: Any) -> _EmptyResult:
        self.page_statement = statement
        return _EmptyResult()


@pytest.mark.asyncio
async def test_run_records_skips_recovery_lookup_for_regular_page() -> None:
    session = _StatementCapture()
    repository = OperationsRepository(session)  # type: ignore[arg-type]

    rows, total = await repository.run_records(
        since=datetime(2026, 6, 1, tzinfo=UTC),
        run_type=None,
        status=None,
        batch_id=None,
        unresolved_only=False,
        offset=0,
        limit=20,
    )

    assert rows == []
    assert total == 0
    count_sql = str(
        session.count_statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "EXISTS" not in count_sql

    page_from = session.page_statement.get_final_froms()[0]
    assert page_from.name == "page_runs"
    assert "recovered" not in page_from.c
    assert page_from.element._limit_clause.value == 20
    page_sql = str(
        session.page_statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "EXISTS" not in page_sql


@pytest.mark.asyncio
async def test_unresolved_run_records_apply_recovery_lookup_before_pagination() -> None:
    session = _StatementCapture()
    repository = OperationsRepository(session)  # type: ignore[arg-type]

    await repository.run_records(
        since=datetime(2026, 6, 1, tzinfo=UTC),
        run_type="processing",
        status="failed",
        batch_id=None,
        unresolved_only=True,
        offset=0,
        limit=20,
    )

    count_sql = str(
        session.count_statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "EXISTS" in count_sql
    assert "enriched_runs.recovered IS false" in count_sql
    page_from = session.page_statement.get_final_froms()[0]
    assert page_from.name == "page_runs"
    assert page_from.element._limit_clause.value == 20


@pytest.mark.asyncio
async def test_processing_queue_hides_tasks_covered_by_a_newer_release() -> None:
    session = _StatementCapture()
    repository = OperationsRepository(session)  # type: ignore[arg-type]

    await repository.processing_queue(
        status=None,
        dataset_name=None,
        offset=0,
        limit=20,
    )

    sql = str(
        session.page_statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "dataset_release" in sql
    assert "published_at > coalesce" in sql
    assert "processing_task.status IN" in sql


@pytest.mark.asyncio
async def test_alerts_hide_dependency_blocks_and_expected_duplicate_warnings() -> None:
    session = _StatementCapture()
    repository = OperationsRepository(session)  # type: ignore[arg-type]

    await repository.alert_rows(
        since=datetime(2026, 6, 1, tzinfo=UTC),
        source=None,
        offset=0,
        limit=20,
    )

    sql = str(
        session.page_statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "processing_task.status = 'FAILED'" in sql
    assert "processing_task.status = 'BLOCKED'" not in sql
    assert "dc_concept_cons" in sql
    assert "business_date IS NOT DISTINCT FROM" in sql
    assert "完全重复记录" in sql
    assert "证券历史代码映射为现行代码" in sql
    assert "名称或数值精度差异" in sql
