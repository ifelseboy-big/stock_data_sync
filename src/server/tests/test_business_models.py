from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.base import Base
from app.models import import_all_models

BUSINESS_TABLES = {
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
}

PARTITIONED_TABLES = {
    "stock_daily",
    "stock_technical_daily",
    "stock_moneyflow_daily",
    "market_theme_member_daily",
    "etf_daily",
    "etf_share_size_daily",
}


def test_all_business_tables_are_registered() -> None:
    import_all_models()

    assert BUSINESS_TABLES <= set(Base.metadata.tables)
    assert len(BUSINESS_TABLES) == 28


def test_expected_tables_are_monthly_partitioned() -> None:
    import_all_models()

    actual = {
        table_name
        for table_name in BUSINESS_TABLES
        if Base.metadata.tables[table_name].dialect_options["postgresql"].get("partition_by")
    }
    assert actual == PARTITIONED_TABLES
    for table_name in PARTITIONED_TABLES:
        table = Base.metadata.tables[table_name]
        assert table.dialect_options["postgresql"]["partition_by"] == "RANGE (trade_date)"
        assert "trade_date" in {column.name for column in table.primary_key.columns}


def test_business_schema_compiles_for_postgresql() -> None:
    import_all_models()
    dialect = postgresql.dialect()

    for table_name in BUSINESS_TABLES:
        table = Base.metadata.tables[table_name]
        assert str(CreateTable(table).compile(dialect=dialect))
        for index in table.indexes:
            assert str(CreateIndex(index).compile(dialect=dialect))


def test_provider_optional_board_code_is_not_part_of_moneyflow_identity() -> None:
    import_all_models()
    table = Base.metadata.tables["ths_board_moneyflow_daily"]

    assert {column.name for column in table.primary_key.columns} == {
        "board_type",
        "board_name",
        "trade_date",
    }
    assert table.c.ts_code.nullable is True


def test_theme_members_do_not_require_same_day_theme_ranking_parent() -> None:
    import_all_models()
    table = Base.metadata.tables["market_theme_member_daily"]

    foreign_targets = {foreign_key.target_fullname for foreign_key in table.foreign_keys}
    assert foreign_targets == {"stock.ts_code"}


def test_postgresql_identifiers_fit_length_limit() -> None:
    import_all_models()

    names = {
        named_object.name
        for table_name in BUSINESS_TABLES
        for named_object in (
            *Base.metadata.tables[table_name].constraints,
            *Base.metadata.tables[table_name].indexes,
        )
        if named_object.name is not None
    }
    assert max(map(len, names)) <= 63
