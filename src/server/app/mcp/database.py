from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, Final, NoReturn

from sqlalchemy import bindparam, event, text
from sqlalchemy.engine import Result, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.sql import Select, visitors
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import ColumnClause, TextClause
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.sql.operators import custom_op
from sqlalchemy.sql.selectable import TableClause

from app.core.config import Settings, settings

MCP_READABLE_TABLES: Final[tuple[str, ...]] = (
    "trade_calendar",
    "stock",
    "stock_company",
    "stock_daily",
    "stock_technical_daily",
    "stock_moneyflow_daily",
    "ths_board_moneyflow_daily",
    "stock_suspend_daily",
    "concept_board",
    "concept_board_daily",
    "concept_board_member",
    "theme_index",
    "theme_index_daily",
    "theme_index_member",
    "stock_hot_rank_daily",
    "market_theme_daily",
    "market_theme_member_daily",
    "stock_top_list_daily",
    "stock_top_inst_daily",
    "stock_limit_event_daily",
    "stock_limit_step_daily",
    "market_index",
    "market_index_daily",
    "index_daily_basic",
    "market_index_weight",
    "etf",
    "etf_daily",
    "etf_share_size_daily",
    "dataset_release",
)
MCP_ALLOWED_SQL_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        "abs",
        "avg",
        "ceil",
        "char_length",
        "coalesce",
        "count",
        "date_trunc",
        "dense_rank",
        "exp",
        "first_value",
        "floor",
        "greatest",
        "lag",
        "last_value",
        "lead",
        "least",
        "length",
        "lower",
        "max",
        "min",
        "nullif",
        "power",
        "rank",
        "round",
        "row_number",
        "sqrt",
        "sum",
        "upper",
    }
)


class McpReadOnlyConfigurationError(RuntimeError):
    """Raised when the MCP database boundary is not safely configured."""


class McpReadOnlyViolation(RuntimeError):
    """Raised when MCP code attempts to execute a write operation."""


def _validate_read_only_statement(statement: object) -> None:
    if not isinstance(statement, Select):
        raise McpReadOnlyViolation("MCP database access only allows SQLAlchemy SELECT statements")

    for element in visitors.iterate(statement):
        if any(
            getattr(element, attribute, None)
            for attribute in ("_prefixes", "_suffixes", "_hints", "_statement_hints")
        ):
            raise McpReadOnlyViolation("MCP SELECT statements cannot contain SQL prefixes or hints")
        if isinstance(element, (Insert, Update, Delete, TextClause)):
            raise McpReadOnlyViolation(
                "MCP SELECT statements cannot contain DML or raw SQL fragments"
            )
        if isinstance(element, Select) and getattr(element, "_for_update_arg", None) is not None:
            raise McpReadOnlyViolation("MCP SELECT statements cannot acquire row locks")
        if isinstance(element, ColumnClause) and element.is_literal and element.name != "*":
            raise McpReadOnlyViolation("MCP SELECT statements cannot contain literal SQL columns")
        if isinstance(element, FunctionElement):
            function_name = str(element.name).lower()
            package_names = tuple(getattr(element, "packagenames", ()))
            if package_names or function_name not in MCP_ALLOWED_SQL_FUNCTIONS:
                raise McpReadOnlyViolation(f"MCP SELECT function is not allowed: {function_name}")
        if isinstance(getattr(element, "operator", None), custom_op):
            raise McpReadOnlyViolation("MCP SELECT statements cannot contain custom SQL operators")
        if isinstance(element, TableClause):
            table_name = str(element.name)
            schema_name = str(element.schema) if element.schema is not None else None
            if table_name not in MCP_READABLE_TABLES or schema_name not in (None, "public"):
                raise McpReadOnlyViolation(
                    f"MCP SELECT table is not allowed: {schema_name or 'public'}.{table_name}"
                )


class McpReadOnlySyncSession(Session):
    """Internal SQLAlchemy session with all public write paths disabled."""

    def commit(self) -> NoReturn:
        raise McpReadOnlyViolation("MCP database sessions cannot commit")

    def connection(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise McpReadOnlyViolation("MCP database sessions cannot expose raw connections")

    def bulk_insert_mappings(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise McpReadOnlyViolation("MCP database sessions cannot perform bulk writes")

    def bulk_update_mappings(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise McpReadOnlyViolation("MCP database sessions cannot perform bulk writes")

    def bulk_save_objects(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise McpReadOnlyViolation("MCP database sessions cannot perform bulk writes")


class McpReadOnlyAsyncSession(AsyncSession):
    """Async adapter whose raw-session escape hatches are disabled."""

    async def commit(self) -> NoReturn:
        raise McpReadOnlyViolation("MCP database sessions cannot commit")

    async def connection(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise McpReadOnlyViolation("MCP database sessions cannot expose raw connections")

    async def run_sync(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise McpReadOnlyViolation("MCP database sessions cannot run arbitrary synchronous code")


@event.listens_for(McpReadOnlySyncSession, "do_orm_execute")
def _reject_non_select(execute_state: ORMExecuteState) -> None:
    _validate_read_only_statement(execute_state.statement)


@event.listens_for(McpReadOnlySyncSession, "before_flush")
def _reject_flush(
    session: Session,
    _flush_context: object,
    _instances: object | None,
) -> None:
    if session.new or session.dirty or session.deleted:
        raise McpReadOnlyViolation("MCP database sessions cannot flush data changes")


@event.listens_for(McpReadOnlySyncSession, "before_commit")
def _reject_commit(_session: Session) -> NoReturn:
    raise McpReadOnlyViolation("MCP database sessions cannot commit")


class McpReadOnlyQuery:
    """The only database capability exposed to MCP repositories and tools."""

    __slots__ = ("__session",)

    def __init__(self, session: AsyncSession) -> None:
        self.__session = session

    async def execute(self, statement: Select[Any]) -> Result[Any]:
        _validate_read_only_statement(statement)
        return await self.__session.execute(statement)


def mcp_read_only_connect_options(config: Settings) -> str:
    timeout_ms = config.mcp_query_timeout_seconds * 1000
    return " ".join(
        (
            "-c default_transaction_read_only=on",
            "-c search_path=pg_catalog,public",
            f"-c statement_timeout={timeout_ms}",
            f"-c idle_in_transaction_session_timeout={timeout_ms}",
            "-c application_name=stock-data-mcp",
        )
    )


def create_mcp_read_only_engine(config: Settings) -> AsyncEngine:
    database_url = config.mcp_database_url
    if not database_url:
        raise McpReadOnlyConfigurationError(
            "MCP_DATABASE_URL must be configured with a dedicated read-only PostgreSQL role"
        )

    url = make_url(database_url)
    if url.get_backend_name() != "postgresql" or url.get_driver_name() != "psycopg":
        raise McpReadOnlyConfigurationError(
            "MCP_DATABASE_URL must use the postgresql+psycopg driver"
        )
    if url.username == make_url(config.database_url).username:
        raise McpReadOnlyConfigurationError(
            "MCP_DATABASE_URL must use a database role distinct from DATABASE_URL"
        )

    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        connect_args={"options": mcp_read_only_connect_options(config)},
    )


def _role_security_failures(
    role: Mapping[str, Any],
    *,
    expected_role: str,
) -> list[str]:
    failures: list[str] = []
    if role["role_name"] != expected_role:
        failures.append("connected database role does not match MCP_DATABASE_URL")
    if role["transaction_read_only"] != "on":
        failures.append("transaction_read_only is not enabled")
    for key in (
        "rolsuper",
        "rolinherit",
        "rolcreaterole",
        "rolcreatedb",
        "rolreplication",
        "rolbypassrls",
        "has_membership",
        "can_create_database_objects",
        "can_create_temp_tables",
        "can_create_schema_objects",
    ):
        if role[key]:
            failures.append(f"unsafe database capability is enabled: {key}")
    if not role["rolcanlogin"]:
        failures.append("MCP database role cannot login")
    return failures


async def verify_mcp_database_security(
    engine: AsyncEngine,
    config: Settings,
) -> None:
    expected_role = make_url(config.mcp_database_url or "").username
    if expected_role is None:
        raise McpReadOnlyConfigurationError("MCP_DATABASE_URL does not contain a database role")

    role_statement = text(
        """
        SELECT
            current_user AS role_name,
            current_setting('transaction_read_only') AS transaction_read_only,
            role_record.rolsuper,
            role_record.rolinherit,
            role_record.rolcreaterole,
            role_record.rolcreatedb,
            role_record.rolcanlogin,
            role_record.rolreplication,
            role_record.rolbypassrls,
            EXISTS (
                SELECT 1 FROM pg_auth_members membership
                WHERE membership.member = role_record.oid
            ) AS has_membership,
            has_database_privilege(current_user, current_database(), 'CREATE')
                AS can_create_database_objects,
            has_database_privilege(current_user, current_database(), 'TEMP')
                AS can_create_temp_tables,
            has_schema_privilege(current_user, 'public', 'CREATE')
                AS can_create_schema_objects,
            current_schemas(false) AS effective_search_path
        FROM pg_roles role_record
        WHERE role_record.rolname = current_user
        """
    )
    write_privileges_statement = text(
        """
        SELECT namespace.nspname || '.' || relation.relname
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname NOT LIKE 'pg_%'
          AND namespace.nspname <> 'information_schema'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND (
              has_table_privilege(current_user, relation.oid, 'INSERT')
              OR has_table_privilege(current_user, relation.oid, 'UPDATE')
              OR has_table_privilege(current_user, relation.oid, 'DELETE')
              OR has_table_privilege(current_user, relation.oid, 'TRUNCATE')
              OR has_table_privilege(current_user, relation.oid, 'REFERENCES')
              OR has_table_privilege(current_user, relation.oid, 'TRIGGER')
          )
        ORDER BY 1
        """
    )
    sequence_privileges_statement = text(
        """
        SELECT namespace.nspname || '.' || relation.relname
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname NOT LIKE 'pg_%'
          AND namespace.nspname <> 'information_schema'
          AND relation.relkind = 'S'
          AND (
              has_sequence_privilege(current_user, relation.oid, 'USAGE')
              OR has_sequence_privilege(current_user, relation.oid, 'UPDATE')
          )
        ORDER BY 1
        """
    )
    function_privileges_statement = text(
        """
        SELECT namespace.nspname || '.' || routine.proname
        FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname NOT LIKE 'pg_%'
          AND namespace.nspname <> 'information_schema'
          AND has_function_privilege(current_user, routine.oid, 'EXECUTE')
        ORDER BY 1
        """
    )
    readable_tables_statement = text(
        """
        SELECT relation.relname
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname IN :table_names
          AND has_table_privilege(current_user, relation.oid, 'SELECT')
        """
    ).bindparams(bindparam("table_names", expanding=True))
    unsafe_schemas_statement = text(
        """
        SELECT namespace.nspname
        FROM pg_namespace namespace
        WHERE namespace.nspname NOT LIKE 'pg_%'
          AND namespace.nspname <> 'information_schema'
          AND (
              namespace.nspowner = (
                  SELECT role_record.oid
                  FROM pg_roles role_record
                  WHERE role_record.rolname = current_user
              )
              OR has_schema_privilege(current_user, namespace.oid, 'CREATE')
          )
        ORDER BY 1
        """
    )
    readable_relations_statement = text(
        """
        SELECT namespace.nspname || '.' || relation.relname
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname NOT LIKE 'pg_%'
          AND namespace.nspname <> 'information_schema'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND has_table_privilege(current_user, relation.oid, 'SELECT')
        ORDER BY 1
        """
    )

    try:
        async with engine.connect() as connection:
            role = (await connection.execute(role_statement)).mappings().one()
            write_relations = tuple((await connection.scalars(write_privileges_statement)).all())
            writable_sequences = tuple(
                (await connection.scalars(sequence_privileges_statement)).all()
            )
            executable_functions = tuple(
                (await connection.scalars(function_privileges_statement)).all()
            )
            unsafe_schemas = tuple((await connection.scalars(unsafe_schemas_statement)).all())
            readable_relations = set((await connection.scalars(readable_relations_statement)).all())
            readable_tables = set(
                (
                    await connection.scalars(
                        readable_tables_statement,
                        {"table_names": MCP_READABLE_TABLES},
                    )
                ).all()
            )
    except McpReadOnlyConfigurationError:
        raise
    except Exception as exc:
        raise McpReadOnlyConfigurationError(
            "MCP database connection or security verification failed"
        ) from exc

    failures = _role_security_failures(dict(role), expected_role=expected_role)
    if tuple(role["effective_search_path"]) != ("pg_catalog", "public"):
        failures.append("effective search_path must be pg_catalog,public")
    if unsafe_schemas:
        failures.append(
            "CREATE or ownership privileges exist on schemas: " + ", ".join(unsafe_schemas)
        )
    if write_relations:
        failures.append("write privileges exist on: " + ", ".join(write_relations))
    if writable_sequences:
        failures.append("write privileges exist on sequences: " + ", ".join(writable_sequences))
    if executable_functions:
        failures.append(
            "EXECUTE privileges exist on public functions: " + ", ".join(executable_functions)
        )
    missing_tables = sorted(set(MCP_READABLE_TABLES) - readable_tables)
    if missing_tables:
        failures.append("SELECT privilege is missing on: " + ", ".join(missing_tables))
    allowed_relations = {f"public.{table_name}" for table_name in MCP_READABLE_TABLES}
    unauthorized_relations = sorted(readable_relations - allowed_relations)
    if unauthorized_relations:
        failures.append(
            "unauthorized SELECT privilege exists on: " + ", ".join(unauthorized_relations)
        )
    if failures:
        raise McpReadOnlyConfigurationError("; ".join(failures))


@lru_cache
def get_mcp_read_only_engine() -> AsyncEngine:
    return create_mcp_read_only_engine(settings)


@lru_cache
def get_mcp_read_only_session_factory() -> async_sessionmaker[McpReadOnlyAsyncSession]:
    return async_sessionmaker(
        bind=get_mcp_read_only_engine(),
        class_=McpReadOnlyAsyncSession,
        sync_session_class=McpReadOnlySyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


_database_security_verified = False


async def initialize_mcp_read_only_database() -> None:
    global _database_security_verified
    if _database_security_verified:
        return
    await verify_mcp_database_security(get_mcp_read_only_engine(), settings)
    _database_security_verified = True


@asynccontextmanager
async def mcp_read_only_query() -> AsyncIterator[McpReadOnlyQuery]:
    """Yield the only database capability available to MCP tools."""

    await initialize_mcp_read_only_database()
    factory = get_mcp_read_only_session_factory()
    async with factory() as session:
        try:
            yield McpReadOnlyQuery(session)
        finally:
            await session.rollback()


async def dispose_mcp_read_only_engine() -> None:
    global _database_security_verified
    if get_mcp_read_only_engine.cache_info().currsize:
        await get_mcp_read_only_engine().dispose()
    get_mcp_read_only_session_factory.cache_clear()
    get_mcp_read_only_engine.cache_clear()
    _database_security_verified = False
