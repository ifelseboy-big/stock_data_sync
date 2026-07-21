import ast
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import (
    Integer,
    String,
    create_engine,
    func,
    insert,
    literal_column,
    select,
    table,
    text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings
from app.mcp.database import (
    McpReadOnlyAsyncSession,
    McpReadOnlyConfigurationError,
    McpReadOnlyQuery,
    McpReadOnlySyncSession,
    McpReadOnlyViolation,
    create_mcp_read_only_engine,
    mcp_read_only_connect_options,
)


class ModelBase(DeclarativeBase):
    pass


class Record(ModelBase):
    __tablename__ = "stock"

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(String(32), nullable=False)


@pytest.fixture
def database_engine() -> Generator[Engine, None, None]:
    engine = create_engine("sqlite://")
    ModelBase.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            Record.__table__.insert(),
            [{"record_id": 1, "value": "existing"}],
        )
    try:
        yield engine
    finally:
        engine.dispose()


def test_mcp_session_allows_orm_selects(database_engine: Engine) -> None:
    with McpReadOnlySyncSession(bind=database_engine) as session:
        assert session.scalar(select(Record.value)) == "existing"


def test_mcp_session_rejects_orm_writes(database_engine: Engine) -> None:
    with McpReadOnlySyncSession(bind=database_engine) as session:
        with pytest.raises(McpReadOnlyViolation, match="only allows SQLAlchemy SELECT"):
            session.execute(update(Record).values(value="changed"))


def test_mcp_session_rejects_pending_changes(database_engine: Engine) -> None:
    with McpReadOnlySyncSession(bind=database_engine) as session:
        session.add(Record(record_id=2, value="new"))
        with pytest.raises(McpReadOnlyViolation, match="cannot flush"):
            session.flush()


def test_mcp_session_rejects_raw_sql(database_engine: Engine) -> None:
    with McpReadOnlySyncSession(bind=database_engine) as session:
        with pytest.raises(McpReadOnlyViolation, match="only allows SQLAlchemy SELECT"):
            session.execute(text("SELECT 1"))


def test_mcp_session_rejects_select_for_update(database_engine: Engine) -> None:
    with McpReadOnlySyncSession(bind=database_engine) as session:
        with pytest.raises(McpReadOnlyViolation, match="cannot acquire row locks"):
            session.execute(select(Record).with_for_update())


def test_mcp_session_rejects_nested_write_cte(database_engine: Engine) -> None:
    write_cte = insert(Record).values(record_id=2, value="new").cte("write_record")
    statement = select(Record.record_id).add_cte(write_cte)

    with McpReadOnlySyncSession(bind=database_engine) as session:
        with pytest.raises(McpReadOnlyViolation, match="cannot contain DML"):
            session.execute(statement)


def test_mcp_session_rejects_nested_select_for_update(database_engine: Engine) -> None:
    locked_records = select(Record).with_for_update().cte("locked_records")
    statement = select(locked_records.c.record_id)

    with McpReadOnlySyncSession(bind=database_engine) as session:
        with pytest.raises(McpReadOnlyViolation, match="cannot acquire row locks"):
            session.execute(statement)


@pytest.mark.parametrize(
    "statement",
    (
        select(literal_column("pg_advisory_lock(731500001)")),
        select(func.pg_advisory_lock(731500001)),
        select(func.pg_notify("stock_data", "changed")),
    ),
)
def test_mcp_session_rejects_raw_and_side_effecting_select_expressions(
    database_engine: Engine,
    statement: object,
) -> None:
    with McpReadOnlySyncSession(bind=database_engine) as session:
        with pytest.raises(McpReadOnlyViolation):
            session.execute(statement)  # type: ignore[arg-type]


def test_mcp_session_allows_approved_aggregate_functions(database_engine: Engine) -> None:
    with McpReadOnlySyncSession(bind=database_engine) as session:
        assert session.scalar(select(func.count(Record.record_id))) == 1


@pytest.mark.parametrize(
    "statement",
    (
        select(Record.record_id).prefix_with("pg_advisory_lock(731500001),"),
        select(Record.record_id).where(Record.record_id.op("IS NOT DISTINCT FROM")(1)),
    ),
)
def test_mcp_session_rejects_prefixes_and_custom_operators(
    database_engine: Engine,
    statement: object,
) -> None:
    with McpReadOnlySyncSession(bind=database_engine) as session:
        with pytest.raises(McpReadOnlyViolation):
            session.execute(statement)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "statement",
    (
        select(table("collection_task")),
        select(table("pg_class", schema="pg_catalog")),
    ),
)
def test_mcp_session_rejects_non_business_and_system_tables(
    database_engine: Engine,
    statement: object,
) -> None:
    with McpReadOnlySyncSession(bind=database_engine) as session:
        with pytest.raises(McpReadOnlyViolation, match="table is not allowed"):
            session.execute(statement)  # type: ignore[arg-type]


def test_mcp_session_rejects_commit_connection_and_bulk_writes(
    database_engine: Engine,
) -> None:
    with McpReadOnlySyncSession(bind=database_engine) as session:
        with pytest.raises(McpReadOnlyViolation, match="cannot commit"):
            session.commit()
        with pytest.raises(McpReadOnlyViolation, match="raw connections"):
            session.connection()
        with pytest.raises(McpReadOnlyViolation, match="bulk writes"):
            session.bulk_insert_mappings(
                Record,
                [{"record_id": 2, "value": "new"}],
            )


@pytest.mark.asyncio
async def test_mcp_async_session_rejects_escape_hatches() -> None:
    session = McpReadOnlyAsyncSession(sync_session_class=McpReadOnlySyncSession)
    try:
        with pytest.raises(McpReadOnlyViolation, match="cannot commit"):
            await session.commit()
        with pytest.raises(McpReadOnlyViolation, match="raw connections"):
            await session.connection()
        with pytest.raises(McpReadOnlyViolation, match="arbitrary synchronous code"):
            await session.run_sync(lambda _session: None)
    finally:
        await session.close()


def test_mcp_query_facade_only_exposes_select_execution() -> None:
    public_capabilities = {name for name in dir(McpReadOnlyQuery) if not name.startswith("_")}

    assert public_capabilities == {"execute"}


def test_mcp_modules_do_not_import_application_write_sessions() -> None:
    server_dir = Path(__file__).resolve().parents[1]
    roots = (server_dir / "app" / "mcp", server_dir / "app" / "modules" / "market_query")
    forbidden = {"app.db.session", "app.db.sync_session"}
    violations: list[str] = []

    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in forbidden:
                    violations.append(f"{path}:{node.lineno}:{node.module}")
                if isinstance(node, ast.Import):
                    violations.extend(
                        f"{path}:{node.lineno}:{alias.name}"
                        for alias in node.names
                        if alias.name in forbidden
                    )

    assert violations == []


def test_mcp_engine_requires_a_dedicated_database_url() -> None:
    config = settings.model_copy(update={"mcp_database_url": None})

    with pytest.raises(McpReadOnlyConfigurationError, match="MCP_DATABASE_URL"):
        create_mcp_read_only_engine(config)


def test_mcp_engine_requires_psycopg_postgresql() -> None:
    config = settings.model_copy(update={"mcp_database_url": "sqlite+aiosqlite://"})

    with pytest.raises(McpReadOnlyConfigurationError, match=r"postgresql\+psycopg"):
        create_mcp_read_only_engine(config)


def test_mcp_engine_requires_a_distinct_database_role() -> None:
    config = settings.model_copy(
        update={
            "database_url": "postgresql+psycopg://writer:secret@localhost/database",
            "mcp_database_url": "postgresql+psycopg://writer:other@localhost/database",
        }
    )

    with pytest.raises(McpReadOnlyConfigurationError, match="role distinct"):
        create_mcp_read_only_engine(config)


@pytest.mark.asyncio
async def test_mcp_engine_accepts_a_distinct_read_only_role() -> None:
    config = settings.model_copy(
        update={
            "database_url": "postgresql+psycopg://writer:secret@localhost/database",
            "mcp_database_url": "postgresql+psycopg://reader:secret@localhost/database",
        }
    )

    engine = create_mcp_read_only_engine(config)
    try:
        assert engine.url.username == "reader"
    finally:
        await engine.dispose()


def test_mcp_connection_enforces_read_only_and_timeouts() -> None:
    config = settings.model_copy(update={"mcp_query_timeout_seconds": 17})

    options = mcp_read_only_connect_options(config)

    assert "default_transaction_read_only=on" in options
    assert "search_path=pg_catalog,public" in options
    assert "statement_timeout=17000" in options
    assert "idle_in_transaction_session_timeout=17000" in options
