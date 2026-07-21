from __future__ import annotations

import asyncio
from typing import Any

from psycopg import Connection, sql
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.mcp.database import (
    MCP_READABLE_TABLES,
    McpReadOnlyConfigurationError,
    dispose_mcp_read_only_engine,
    initialize_mcp_read_only_database,
)

MCP_ROLE = "stock_mcp_reader"


def _connection_info() -> str:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "postgresql" or not url.username or not url.database:
        raise McpReadOnlyConfigurationError("DATABASE_URL must be a PostgreSQL administrator URL")
    values: dict[str, Any] = {
        "user": url.username,
        "dbname": url.database,
    }
    if url.password:
        values["password"] = url.password
    if url.host:
        values["host"] = url.host
    if url.port:
        values["port"] = url.port
    return make_conninfo(**values)


def provision_mcp_reader() -> None:
    reader_url = make_url(settings.mcp_database_url or "")
    if reader_url.username != MCP_ROLE or not reader_url.password:
        raise McpReadOnlyConfigurationError(
            f"MCP_DATABASE_URL must contain role {MCP_ROLE} and a non-empty password"
        )
    database_name = make_url(settings.database_url).database
    if not database_name or reader_url.database != database_name:
        raise McpReadOnlyConfigurationError(
            "MCP_DATABASE_URL and DATABASE_URL must select the same database"
        )

    with Connection.connect(_connection_info()) as connection:
        role_exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (MCP_ROLE,)
        ).fetchone()
        if role_exists is None:
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(MCP_ROLE), sql.Literal(reader_url.password)
                )
            )
        connection.execute(
            sql.SQL(
                "ALTER ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(MCP_ROLE), sql.Literal(reader_url.password))
        )
        connection.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(
                sql.Identifier(MCP_ROLE)
            )
        )
        connection.execute(
            sql.SQL("ALTER ROLE {} SET statement_timeout = {}").format(
                sql.Identifier(MCP_ROLE),
                sql.Literal(f"{settings.mcp_query_timeout_seconds}s"),
            )
        )
        connection.execute(
            sql.SQL("ALTER ROLE {} SET search_path = pg_catalog, public").format(
                sql.Identifier(MCP_ROLE)
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(
                sql.Identifier(database_name), sql.Identifier(MCP_ROLE)
            )
        )
        connection.execute(
            sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(database_name)
            )
        )
        connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA public FROM {}").format(
                sql.Identifier(MCP_ROLE)
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(
                sql.Identifier(MCP_ROLE)
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}").format(
                sql.Identifier(MCP_ROLE)
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM {}").format(
                sql.Identifier(MCP_ROLE)
            )
        )
        connection.execute("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")
        connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
            ).format(sql.Identifier(make_url(settings.database_url).username or ""))
        )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database_name), sql.Identifier(MCP_ROLE)
            )
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(MCP_ROLE))
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                sql.SQL(", ").join(sql.Identifier(table) for table in MCP_READABLE_TABLES),
                sql.Identifier(MCP_ROLE),
            )
        )


async def _verify_reader() -> None:
    await initialize_mcp_read_only_database()
    await dispose_mcp_read_only_engine()


def main() -> None:
    provision_mcp_reader()
    asyncio.run(_verify_reader())
    print("MCP只读数据库角色已配置并收敛权限")


if __name__ == "__main__":
    main()
