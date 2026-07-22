import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url

from app.core.config import settings

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an isolated migrated PostgreSQL database",
)

SERVER_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture
def migration_database() -> Iterator[tuple[Engine, str]]:
    admin_engine = create_engine(settings.database_url)
    schema = f"migration_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    url = make_url(settings.database_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    database_url = url.render_as_string(hide_password=False).replace("%3D", "=")
    migration_engine = create_engine(database_url)
    try:
        yield migration_engine, database_url
    finally:
        migration_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _alembic(
    database_url: str,
    *arguments: str,
    succeeds: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=SERVER_DIR,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if succeeds and result.returncode != 0:
        raise AssertionError(result.stderr)
    if not succeeds and result.returncode == 0:
        raise AssertionError("migration unexpectedly succeeded")
    return result


def _prepare_old_schema(engine: Engine, database_url: str) -> None:
    _alembic(database_url, "upgrade", "20260720_0010")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO stock (
                    ts_code, symbol, name, exchange, list_status, synced_at
                ) VALUES (
                    'T999998.TEST', 'T999998', '迁移测试股票', 'TEST', 'L', now()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO ths_board_moneyflow_daily (
                    board_type, ts_code, trade_date, board_name, synced_at
                ) VALUES (
                    'CONCEPT', '885001.TI', DATE '2099-01-04', '迁移测试板块', now()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE market_theme_member_daily_p209901
                PARTITION OF market_theme_member_daily
                FOR VALUES FROM ('2099-01-01') TO ('2099-02-01')
                """
            )
        )


def _assert_runtime_recovery_indexes(engine: Engine) -> None:
    with engine.connect() as connection:
        processing_index = connection.scalar(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'processing_task'
                  AND indexname = 'idx_processing_active_recovery'
                """
            )
        )
        collection_claim_index = connection.scalar(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'collection_batch'
                  AND indexname = 'idx_collection_batch_active_claim'
                """
            )
        )
    assert processing_index is not None
    assert "(output_dataset, business_date)" in processing_index
    assert "INCLUDE (source_batch_id, queued_at, started_at)" in processing_index
    assert "WAITING_DEPENDENCY" in processing_index
    assert "BLOCKED" in processing_index
    assert collection_claim_index is not None
    assert "CASE" in collection_claim_index
    assert "business_date" in collection_claim_index
    assert "PENDING" in collection_claim_index


def test_old_schema_upgrades_without_losing_rows(
    migration_database: tuple[Engine, str],
) -> None:
    engine, database_url = migration_database
    _prepare_old_schema(engine, database_url)

    _alembic(database_url, "upgrade", "head")

    inspector = inspect(engine)
    assert inspector.get_pk_constraint("ths_board_moneyflow_daily")[
        "constrained_columns"
    ] == ["board_type", "board_name", "trade_date"]
    assert next(
        column
        for column in inspector.get_columns("ths_board_moneyflow_daily")
        if column["name"] == "ts_code"
    )["nullable"]
    assert "idx_ths_board_flow_code" in {
        index["name"] for index in inspector.get_indexes("ths_board_moneyflow_daily")
    }
    assert "fk_theme_member_theme" not in {
        foreign_key["name"]
        for foreign_key in inspector.get_foreign_keys("market_theme_member_daily")
    }
    _assert_runtime_recovery_indexes(engine)

    with engine.begin() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM ths_board_moneyflow_daily")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260722_0013"
        connection.execute(
            text(
                """
                INSERT INTO ths_board_moneyflow_daily (
                    board_type, board_name, trade_date, ts_code, synced_at
                ) VALUES (
                    'CONCEPT', '供应方无代码板块', DATE '2099-01-04', NULL, now()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO market_theme_member_daily (
                    source, trade_date, theme_code, ts_code, synced_at
                ) VALUES (
                    'DC', DATE '2099-01-04', 'NO_SAME_DAY_PARENT', 'T999998.TEST', now()
                )
                """
            )
        )


def test_empty_schema_upgrades_to_current_runtime_structure(
    migration_database: tuple[Engine, str],
) -> None:
    engine, database_url = migration_database

    _alembic(database_url, "upgrade", "head")

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260722_0013"
        )
    _assert_runtime_recovery_indexes(engine)


def test_new_primary_key_conflict_aborts_without_deleting_rows(
    migration_database: tuple[Engine, str],
) -> None:
    engine, database_url = migration_database
    _prepare_old_schema(engine, database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO ths_board_moneyflow_daily (
                    board_type, ts_code, trade_date, board_name, synced_at
                ) VALUES (
                    'CONCEPT', '885002.TI', DATE '2099-01-04', '迁移测试板块', now()
                )
                """
            )
        )

    result = _alembic(database_url, "upgrade", "head", succeeds=False)

    assert "存在新主键冲突" in result.stderr
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260720_0010"
        assert connection.scalar(text("SELECT COUNT(*) FROM ths_board_moneyflow_daily")) == 2
    assert inspect(engine).get_pk_constraint("ths_board_moneyflow_daily")[
        "constrained_columns"
    ] == ["board_type", "ts_code", "trade_date"]


def test_already_compatible_v020_schema_is_only_stamped(
    migration_database: tuple[Engine, str],
) -> None:
    engine, database_url = migration_database
    _prepare_old_schema(engine, database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE ths_board_moneyflow_daily
                DROP CONSTRAINT pk_ths_board_moneyflow_daily
                """
            )
        )
        connection.execute(
            text(
                """
                ALTER TABLE ths_board_moneyflow_daily
                ALTER COLUMN ts_code DROP NOT NULL,
                ADD CONSTRAINT pk_ths_board_moneyflow_daily
                PRIMARY KEY (board_type, board_name, trade_date)
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE INDEX idx_ths_board_flow_code
                ON ths_board_moneyflow_daily (ts_code, trade_date)
                """
            )
        )
        connection.execute(
            text(
                """
                ALTER TABLE market_theme_member_daily
                DROP CONSTRAINT fk_theme_member_theme
                """
            )
        )

    _alembic(database_url, "upgrade", "head")

    with engine.begin() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260722_0013"
        assert connection.scalar(text("SELECT COUNT(*) FROM ths_board_moneyflow_daily")) == 1
