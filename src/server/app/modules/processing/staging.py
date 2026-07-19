from collections.abc import Mapping, Sequence
from typing import cast
from uuid import uuid4

from psycopg import Connection as PsycopgConnection
from psycopg import sql
from psycopg.types.json import Jsonb
from sqlalchemy import Column, MetaData, Table, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session
from sqlalchemy.sql.schema import Column as SchemaColumn

from app.catalog import WriteStrategy
from app.common.errors import ProcessingError

type PreparedRow = dict[str, object]


class PostgresStagingPublisher:
    def publish(
        self,
        session: Session,
        *,
        target: Table,
        rows: Sequence[PreparedRow],
        strategy: WriteStrategy,
        key_columns: tuple[str, ...],
        update_columns: tuple[str, ...],
        replace_filters: Mapping[str, object] | None = None,
        chunk_size: int = 5_000,
    ) -> int:
        if not key_columns:
            raise ValueError("key_columns must not be empty")
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        columns = key_columns + tuple(
            column for column in update_columns if column not in key_columns
        )
        _require_target_columns(target, columns)

        stage = _create_stage_table(session, target, columns)
        _copy_stage_rows(session, stage, columns, rows, chunk_size=chunk_size)
        self._validate_stage(session, stage, key_columns, expected_rows=len(rows))

        if strategy in {WriteStrategy.MASTER_MERGE, WriteStrategy.UPSERT_KEY}:
            self._merge(session, target, stage, columns, key_columns, update_columns)
            return len(rows)
        if strategy in {WriteStrategy.REPLACE_DATE, WriteStrategy.REPLACE_ENTITY}:
            if not replace_filters:
                raise ValueError(f"{strategy.value} requires replace_filters")
            self._replace(session, target, stage, columns, replace_filters)
            return len(rows)
        if strategy == WriteStrategy.PATCH_COLUMNS:
            return self._patch(session, target, stage, key_columns, update_columns)
        raise ValueError(f"unsupported write strategy: {strategy.value}")

    @staticmethod
    def _validate_stage(
        session: Session,
        stage: Table,
        key_columns: tuple[str, ...],
        *,
        expected_rows: int,
    ) -> None:
        actual_rows = session.scalar(select(func.count()).select_from(stage))
        if actual_rows != expected_rows:
            raise ProcessingError(
                f"staging row-count mismatch: expected {expected_rows}, got {actual_rows}"
            )
        if expected_rows == 0:
            return
        keys = tuple(stage.c[column] for column in key_columns)
        duplicate = session.execute(
            select(*keys).group_by(*keys).having(func.count() > 1).limit(1)
        ).first()
        if duplicate is not None:
            raise ProcessingError(f"duplicate staging key: {tuple(duplicate)}")

    @staticmethod
    def _merge(
        session: Session,
        target: Table,
        stage: Table,
        columns: tuple[str, ...],
        key_columns: tuple[str, ...],
        update_columns: tuple[str, ...],
    ) -> None:
        if not columns:
            return
        statement = insert(target).from_select(
            columns,
            select(*(stage.c[column] for column in columns)),
        )
        if update_columns:
            statement = statement.on_conflict_do_update(
                index_elements=tuple(target.c[column] for column in key_columns),
                set_={column: statement.excluded[column] for column in update_columns},
            )
        else:
            statement = statement.on_conflict_do_nothing(
                index_elements=tuple(target.c[column] for column in key_columns)
            )
        session.execute(statement)

    @staticmethod
    def _replace(
        session: Session,
        target: Table,
        stage: Table,
        columns: tuple[str, ...],
        replace_filters: Mapping[str, object],
    ) -> None:
        _require_target_columns(target, tuple(replace_filters))
        predicate = tuple(
            target.c[column] == value for column, value in replace_filters.items()
        )
        session.execute(delete(target).where(*predicate))
        if columns:
            session.execute(
                insert(target).from_select(
                    columns,
                    select(*(stage.c[column] for column in columns)),
                )
            )

    @staticmethod
    def _patch(
        session: Session,
        target: Table,
        stage: Table,
        key_columns: tuple[str, ...],
        update_columns: tuple[str, ...],
    ) -> int:
        if not update_columns:
            raise ValueError("PATCH_COLUMNS requires update_columns")
        join_predicate = tuple(
            target.c[column] == stage.c[column] for column in key_columns
        )
        result = cast(
            CursorResult[tuple[object, ...]],
            session.execute(
                update(target)
                .where(*join_predicate)
                .values({column: stage.c[column] for column in update_columns})
            ),
        )
        row_count = result.rowcount
        if row_count < 0:
            raise ProcessingError("database did not report patched row count")
        stage_count = session.scalar(select(func.count()).select_from(stage))
        if row_count != stage_count:
            raise ProcessingError(
                f"patch target mismatch: expected {stage_count}, updated {row_count}"
            )
        return row_count


def _create_stage_table(
    session: Session,
    target: Table,
    columns: tuple[str, ...],
) -> Table:
    stage_name = _stage_table_name(target.name)
    stage = Table(
        stage_name,
        MetaData(),
        *(_clone_column(target.c[column]) for column in columns),
        prefixes=("TEMPORARY",),
        postgresql_on_commit="DROP",
    )
    stage.create(session.connection())
    return stage


def _stage_table_name(target_name: str) -> str:
    # PostgreSQL identifiers are limited to 63 bytes. Model/table names in this
    # project are ASCII, so reserving 32 characters for the UUID keeps the name
    # unique while retaining a readable target prefix.
    return f"_stage_{target_name[:23]}_{uuid4().hex}"


def _copy_stage_rows(
    session: Session,
    stage: Table,
    columns: tuple[str, ...],
    rows: Sequence[PreparedRow],
    *,
    chunk_size: int,
) -> None:
    if not rows:
        return
    driver_connection = cast(
        PsycopgConnection[tuple[object, ...]],
        session.connection().connection.driver_connection,
    )
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(stage.name),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
    )
    with driver_connection.cursor() as cursor, cursor.copy(statement) as copy:
        for offset in range(0, len(rows), chunk_size):
            for row in rows[offset : offset + chunk_size]:
                copy.write_row(
                    tuple(
                        _copy_value(stage.c[column], row.get(column))
                        for column in columns
                    )
                )


def _copy_value(column: SchemaColumn[object], value: object) -> object:
    if value is not None and isinstance(column.type, JSONB):
        return Jsonb(value)
    return value


def _clone_column(column: SchemaColumn[object]) -> Column[object]:
    return Column(column.name, column.type, nullable=column.nullable)


def _require_target_columns(target: Table, columns: tuple[str, ...]) -> None:
    missing = set(columns) - set(target.c.keys())
    if missing:
        raise ValueError(f"unknown columns for {target.name}: {sorted(missing)}")
