import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.mcp.database import (
    MCP_READABLE_TABLES,
    create_mcp_read_only_engine,
    verify_mcp_database_security,
)

ADMIN_DATABASE_URL = os.getenv("MCP_TEST_ADMIN_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1" or not ADMIN_DATABASE_URL,
    reason="requires MCP_TEST_ADMIN_DATABASE_URL for an isolated migrated PostgreSQL database",
)


@pytest.mark.asyncio
async def test_mcp_role_and_connection_reject_real_postgresql_writes() -> None:
    admin_url = make_url(ADMIN_DATABASE_URL)
    database_name = admin_url.database
    assert database_name is not None
    assert "test" in database_name.lower(), "MCP integration test requires a test database"

    role_name = f"stock_mcp_test_{uuid4().hex}"
    role_password = uuid4().hex
    admin_engine = create_engine(ADMIN_DATABASE_URL)
    identifier = admin_engine.dialect.identifier_preparer.quote
    role_identifier = identifier(role_name)
    database_identifier = identifier(database_name)
    table_identifiers = ", ".join(identifier(name) for name in MCP_READABLE_TABLES)
    role_created = False

    try:
        with admin_engine.connect() as connection:
            public_function_count = int(
                connection.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_proc routine
                        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
                        WHERE namespace.nspname NOT LIKE 'pg_%'
                          AND namespace.nspname <> 'information_schema'
                          AND has_function_privilege('PUBLIC', routine.oid, 'EXECUTE')
                        """
                    )
                )
                or 0
            )
            public_has_temp = bool(
                connection.scalar(
                    text("SELECT has_database_privilege('PUBLIC', current_database(), 'TEMP')")
                )
            )
            public_has_schema_create = bool(
                connection.scalar(text("SELECT has_schema_privilege('PUBLIC', 'public', 'CREATE')"))
            )
        if public_function_count or public_has_temp or public_has_schema_create:
            pytest.skip("isolated MCP test database has not been hardened")

        with admin_engine.begin() as connection:
            connection.exec_driver_sql(
                f"CREATE ROLE {role_identifier} LOGIN PASSWORD '{role_password}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                "NOREPLICATION NOBYPASSRLS"
            )
            connection.exec_driver_sql(
                f"GRANT CONNECT ON DATABASE {database_identifier} TO {role_identifier}"
            )
            connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {role_identifier}")
            connection.exec_driver_sql(
                f"GRANT SELECT ON TABLE {table_identifiers} TO {role_identifier}"
            )
        role_created = True

        reader_url = admin_url.set(username=role_name, password=role_password)
        reader_config = settings.model_copy(
            update={
                "database_url": ADMIN_DATABASE_URL,
                "mcp_database_url": reader_url.render_as_string(hide_password=False),
            }
        )
        guarded_engine = create_mcp_read_only_engine(reader_config)
        permission_engine = create_async_engine(reader_url)
        try:
            await verify_mcp_database_security(guarded_engine, reader_config)

            async with guarded_engine.connect() as connection:
                assert int(await connection.scalar(text("SELECT count(*) FROM stock")) or 0) >= 0
                with pytest.raises(DBAPIError):
                    await connection.execute(text("UPDATE stock SET name = name WHERE false"))
                await connection.rollback()

            async with permission_engine.connect() as connection:
                with pytest.raises(DBAPIError):
                    await connection.execute(text("UPDATE stock SET name = name WHERE false"))
                await connection.rollback()
                with pytest.raises(DBAPIError):
                    await connection.execute(text("CREATE TEMP TABLE mcp_write_test (id integer)"))
                await connection.rollback()
        finally:
            await guarded_engine.dispose()
            await permission_engine.dispose()
    finally:
        if role_created:
            with admin_engine.begin() as connection:
                connection.exec_driver_sql(f"DROP OWNED BY {role_identifier}")
                connection.exec_driver_sql(f"DROP ROLE {role_identifier}")
        admin_engine.dispose()
