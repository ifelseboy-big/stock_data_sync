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
    assert session.count_statement is None
    page_sql = str(
        session.page_statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "EXISTS" not in page_sql
    assert "count(*) OVER" in page_sql
    assert session.page_statement._limit_clause.value == 20


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

    assert session.count_statement is None
    page_sql = str(
        session.page_statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "EXISTS" in page_sql
    assert "enriched_runs" not in page_sql
    assert "IS false" in page_sql
    assert "processing_task.status IN ('FAILED', 'SKIPPED', 'CANCELLED')" in page_sql
    assert "collection_task AS" not in page_sql
    assert "dataset_release" in page_sql
    assert "scope_type = CASE" in page_sql
    assert "scope_key = CASE" in page_sql
    assert "count(*) OVER" in page_sql
    assert session.page_statement._limit_clause.value == 20


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
        category="all",
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
    assert "dataset_release" in sql
    assert "scope_type = CASE" in sql
    assert "scope_key = CASE" in sql
    assert "processing_task.status = 'BLOCKED'" not in sql
    assert "scope_key" in sql
    assert "完全重复记录" in sql
    assert "证券历史代码映射为现行代码" in sql
    assert "名称或数值精度差异" in sql
    assert "count(*) OVER" in sql
    assert session.count_statement is None


@pytest.mark.asyncio
async def test_action_alerts_do_not_scan_warning_branches() -> None:
    session = _StatementCapture()
    repository = OperationsRepository(session)  # type: ignore[arg-type]

    await repository.alert_rows(
        since=datetime(2026, 6, 1, tzinfo=UTC),
        category="action_required",
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
    assert "DATA_GAP_WARNING" not in sql
    assert "DATA_QUALITY_WARNING" not in sql
    assert "warning:" not in sql
    assert "processing_task.warning_message" not in sql


@pytest.mark.asyncio
async def test_data_gap_alerts_only_scan_collection_warnings() -> None:
    session = _StatementCapture()
    repository = OperationsRepository(session)  # type: ignore[arg-type]

    await repository.alert_rows(
        since=datetime(2026, 6, 1, tzinfo=UTC),
        category="data_gap",
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
    assert "collection_task" in sql
    assert "processing_task" not in sql
    assert "scheduled_job_execution" not in sql
    assert "EXISTS" not in sql


@pytest.mark.asyncio
async def test_dependencies_filter_terminal_tasks_before_aggregation() -> None:
    session = _StatementCapture()
    repository = OperationsRepository(session)  # type: ignore[arg-type]

    await repository.dependencies(
        since=datetime(2026, 6, 1, tzinfo=UTC),
        readiness="attention",
        query=None,
        offset=0,
        limit=20,
    )

    sql = str(
        session.page_statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "processing_task.status NOT IN ('SUCCESS', 'SKIPPED', 'CANCELLED')" in sql
    assert "count(*) OVER" in sql
    assert session.count_statement is None
