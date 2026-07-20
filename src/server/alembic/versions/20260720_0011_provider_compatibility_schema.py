"""preserve data while applying provider compatibility schema

Revision ID: 20260720_0011
Revises: 20260720_0010
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

from alembic import op

revision: str = "20260720_0011"
down_revision: str | None = "20260720_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOARD_FLOW_TABLE = "ths_board_moneyflow_daily"
BOARD_FLOW_PK_OLD = ("board_type", "ts_code", "trade_date")
BOARD_FLOW_PK_NEW = ("board_type", "board_name", "trade_date")
BOARD_FLOW_CODE_INDEX = "idx_ths_board_flow_code"
THEME_MEMBER_TABLE = "market_theme_member_daily"
THEME_PARENT_COLUMNS = ("source", "theme_code", "trade_date")


def _inspector() -> Inspector:
    return sa.inspect(op.get_bind())


def _primary_key() -> tuple[str, tuple[str, ...]]:
    primary_key = _inspector().get_pk_constraint(BOARD_FLOW_TABLE)
    name = primary_key.get("name")
    columns = tuple(primary_key.get("constrained_columns") or ())
    if not name:
        raise RuntimeError(f"{BOARD_FLOW_TABLE} 主键名称缺失，拒绝自动迁移")
    return name, columns


def _theme_parent_foreign_keys() -> list[str]:
    names: list[str] = []
    for foreign_key in _inspector().get_foreign_keys(THEME_MEMBER_TABLE):
        if tuple(foreign_key.get("constrained_columns") or ()) != THEME_PARENT_COLUMNS:
            continue
        if foreign_key.get("referred_table") != "market_theme_daily":
            continue
        name = foreign_key.get("name")
        if not name:
            raise RuntimeError(f"{THEME_MEMBER_TABLE} 同日题材外键名称缺失，拒绝自动迁移")
        names.append(name)
    return names


def _assert_no_new_key_duplicates() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            """
            SELECT board_type, board_name, trade_date, COUNT(*) AS row_count
            FROM ths_board_moneyflow_daily
            GROUP BY board_type, board_name, trade_date
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).mappings().first()
    if duplicate is not None:
        raise RuntimeError(
            "ths_board_moneyflow_daily 存在新主键冲突，迁移未修改数据："
            f"{dict(duplicate)}"
        )


def _assert_board_flow_code_index() -> None:
    indexes = {index["name"]: index for index in _inspector().get_indexes(BOARD_FLOW_TABLE)}
    existing = indexes.get(BOARD_FLOW_CODE_INDEX)
    if existing is None:
        op.create_index(
            BOARD_FLOW_CODE_INDEX,
            BOARD_FLOW_TABLE,
            ["ts_code", "trade_date"],
            unique=False,
        )
        return
    columns = tuple(existing.get("column_names") or ())
    if columns != ("ts_code", "trade_date") or existing.get("unique") is True:
        raise RuntimeError(
            f"{BOARD_FLOW_CODE_INDEX} 定义异常，迁移未覆盖现有索引：{columns}"
        )


def _make_board_code_nullable() -> None:
    ts_code = next(
        column
        for column in _inspector().get_columns(BOARD_FLOW_TABLE)
        if column["name"] == "ts_code"
    )
    if not ts_code["nullable"]:
        op.alter_column(
            BOARD_FLOW_TABLE,
            "ts_code",
            existing_type=sa.String(length=20),
            nullable=True,
        )


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"LOCK TABLE {BOARD_FLOW_TABLE} IN ACCESS EXCLUSIVE MODE"))

    primary_key_name, primary_key_columns = _primary_key()
    if primary_key_columns == BOARD_FLOW_PK_OLD:
        _assert_no_new_key_duplicates()
        op.drop_constraint(primary_key_name, BOARD_FLOW_TABLE, type_="primary")
        _make_board_code_nullable()
        op.create_primary_key(
            "pk_ths_board_moneyflow_daily",
            BOARD_FLOW_TABLE,
            list(BOARD_FLOW_PK_NEW),
        )
    elif primary_key_columns != BOARD_FLOW_PK_NEW:
        raise RuntimeError(
            f"{BOARD_FLOW_TABLE} 主键结构未知，迁移未修改数据：{primary_key_columns}"
        )

    _make_board_code_nullable()
    _assert_board_flow_code_index()

    for foreign_key_name in _theme_parent_foreign_keys():
        op.drop_constraint(foreign_key_name, THEME_MEMBER_TABLE, type_="foreignkey")


def _assert_downgrade_is_safe() -> None:
    connection = op.get_bind()
    null_code = connection.execute(
        sa.text(
            """
            SELECT board_type, board_name, trade_date
            FROM ths_board_moneyflow_daily
            WHERE ts_code IS NULL
            LIMIT 1
            """
        )
    ).mappings().first()
    if null_code is not None:
        raise RuntimeError(
            "ths_board_moneyflow_daily 已存在空 ts_code，无法无损恢复旧结构："
            f"{dict(null_code)}"
        )

    duplicate = connection.execute(
        sa.text(
            """
            SELECT board_type, ts_code, trade_date, COUNT(*) AS row_count
            FROM ths_board_moneyflow_daily
            GROUP BY board_type, ts_code, trade_date
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).mappings().first()
    if duplicate is not None:
        raise RuntimeError(
            "ths_board_moneyflow_daily 存在旧主键冲突，无法无损恢复旧结构："
            f"{dict(duplicate)}"
        )

    orphan = connection.execute(
        sa.text(
            """
            SELECT member.source, member.theme_code, member.trade_date
            FROM market_theme_member_daily AS member
            WHERE NOT EXISTS (
                SELECT 1
                FROM market_theme_daily AS theme
                WHERE theme.source = member.source
                  AND theme.theme_code = member.theme_code
                  AND theme.trade_date = member.trade_date
            )
            LIMIT 1
            """
        )
    ).mappings().first()
    if orphan is not None:
        raise RuntimeError(
            "market_theme_member_daily 已存在无同日排行的成员，无法无损恢复旧外键："
            f"{dict(orphan)}"
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"LOCK TABLE {BOARD_FLOW_TABLE} IN ACCESS EXCLUSIVE MODE"))
    connection.execute(sa.text(f"LOCK TABLE {THEME_MEMBER_TABLE} IN ACCESS EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE market_theme_daily IN SHARE ROW EXCLUSIVE MODE"))
    _assert_downgrade_is_safe()

    indexes = {index["name"] for index in _inspector().get_indexes(BOARD_FLOW_TABLE)}
    if BOARD_FLOW_CODE_INDEX in indexes:
        op.drop_index(BOARD_FLOW_CODE_INDEX, table_name=BOARD_FLOW_TABLE)

    primary_key_name, primary_key_columns = _primary_key()
    if primary_key_columns == BOARD_FLOW_PK_NEW:
        op.drop_constraint(primary_key_name, BOARD_FLOW_TABLE, type_="primary")
        op.alter_column(
            BOARD_FLOW_TABLE,
            "ts_code",
            existing_type=sa.String(length=20),
            nullable=False,
        )
        op.create_primary_key(
            "pk_ths_board_moneyflow_daily",
            BOARD_FLOW_TABLE,
            list(BOARD_FLOW_PK_OLD),
        )
    elif primary_key_columns != BOARD_FLOW_PK_OLD:
        raise RuntimeError(
            f"{BOARD_FLOW_TABLE} 主键结构未知，迁移未修改数据：{primary_key_columns}"
        )

    if not _theme_parent_foreign_keys():
        op.create_foreign_key(
            "fk_theme_member_theme",
            THEME_MEMBER_TABLE,
            "market_theme_daily",
            list(THEME_PARENT_COLUMNS),
            list(THEME_PARENT_COLUMNS),
        )
