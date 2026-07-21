from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from app.mcp.database import McpReadOnlyQuery, _validate_read_only_statement
from app.mcp.server import mcp
from app.modules.market_query.schemas import ScreenFilter, ScreenOperator, ScreenSort
from app.modules.market_query.service import MarketQueryError, MarketQueryService


class FakeResult:
    def __init__(self, *, scalar: Any = None, rows: list[dict[str, Any]] | None = None) -> None:
        self.scalar = scalar
        self.rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self.scalar

    def mappings(self) -> FakeResult:
        return self

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[Any]:
        if self.rows and set(self.rows[0]) == {"dataset_name"}:
            return [row["dataset_name"] for row in self.rows]
        return self.rows

    def one_or_none(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def first(self) -> dict[str, Any] | None:
        return self.one_or_none()


class FakeQuery:
    def __init__(self, results: list[FakeResult]) -> None:
        self.results = results
        self.statements: list[Select[Any]] = []

    async def execute(self, statement: Select[Any]) -> FakeResult:
        _validate_read_only_statement(statement)
        self.statements.append(statement)
        return self.results.pop(0)


def make_service(fake: FakeQuery) -> MarketQueryService:
    return MarketQueryService(cast(McpReadOnlyQuery, fake))


@pytest.mark.asyncio
async def test_mcp_registers_only_fixed_read_only_tools() -> None:
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {
        "search_securities",
        "get_stock_snapshot",
        "get_stock_history",
        "list_screen_fields",
        "screen_stocks",
        "get_stock_events",
        "get_market_rankings",
        "get_topic",
        "get_index_or_etf",
        "get_data_status",
    }
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.openWorldHint is False


def test_screen_filter_enforces_operator_value_shape() -> None:
    with pytest.raises(ValidationError):
        ScreenFilter(field="valuation.pe_ttm", operator=ScreenOperator.BETWEEN, value=[10])
    with pytest.raises(ValidationError):
        ScreenFilter(field="stock.industry", operator=ScreenOperator.IN, value=[])
    with pytest.raises(ValidationError):
        ScreenFilter(field="valuation.pb", operator=ScreenOperator.IS_NULL, value=None)


@pytest.mark.asyncio
async def test_screen_stocks_uses_release_gate_whitelists_and_hard_limit() -> None:
    fake = FakeQuery(
        [
            FakeResult(scalar=date(2026, 7, 20)),
            FakeResult(
                rows=[
                    {
                        "stock.ts_code": "000001.SZ",
                        "stock.name": "平安银行",
                        "valuation.pe_ttm": Decimal("6.5"),
                    }
                ]
            ),
        ]
    )
    result = await make_service(fake).screen_stocks(
        filters=[
            ScreenFilter(field="valuation.pe_ttm", operator=ScreenOperator.BETWEEN, value=[5, 10])
        ],
        fields=["stock.ts_code", "stock.name", "valuation.pe_ttm"],
        sort=[ScreenSort(field="valuation.pe_ttm", direction="asc")],
        limit=999,
    )
    assert result["ok"] is True
    assert result["meta"]["trade_date"] == "2026-07-20"
    assert result["meta"]["limit"] == 200
    assert result["data"][0]["valuation.pe_ttm"] == "6.5"
    release_sql = str(fake.statements[0].compile(dialect=postgresql.dialect()))
    screen_sql = str(fake.statements[1].compile(dialect=postgresql.dialect()))
    assert "dataset_release" in release_sql
    assert "count(DISTINCT dataset_release.dataset_name)" in release_sql
    assert "stock_daily" in screen_sql
    assert "stock_technical_daily" in screen_sql
    assert "stock_moneyflow_daily" in screen_sql
    assert "stock_suspend_daily" in screen_sql
    assert "BETWEEN" in screen_sql


@pytest.mark.asyncio
async def test_screen_rejects_unknown_field_before_database_query() -> None:
    fake = FakeQuery([])
    with pytest.raises(MarketQueryError) as error:
        await make_service(fake).screen_stocks(fields=["raw.sql"])
    assert error.value.code == "UNSUPPORTED_FIELD"
    assert fake.statements == []


@pytest.mark.asyncio
async def test_history_only_selects_jointly_released_dates() -> None:
    fake = FakeQuery([FakeResult(rows=[])])
    result = await make_service(fake).get_stock_history(
        "000001.sz", adjustment="qfq", include_moneyflow=True, limit=5000
    )
    assert result["meta"]["limit"] == 1000
    assert result["meta"]["datasets"] == [
        "stock_daily.core",
        "stock_technical_daily",
        "stock_moneyflow_daily",
    ]
    compiled = str(fake.statements[0].compile(dialect=postgresql.dialect()))
    assert "stock_technical_daily" in compiled
    assert "stock_moneyflow_daily" in compiled
    assert "dataset_release" in compiled
    assert "count(DISTINCT dataset_release.dataset_name)" in compiled
    assert "close_qfq - stock_technical_daily.pre_close_qfq AS change" in compiled
    assert "historical daily snapshot" in result["meta"]["adjustment_semantics"]


def test_list_screen_fields_is_static_and_does_not_expose_sql() -> None:
    result = make_service(FakeQuery([])).list_screen_fields()
    assert result["ok"] is True
    names = {item["field"] for item in result["data"]}
    assert "market.close" in names
    assert "valuation.pe_ttm" in names
    assert "technical.rsi_6" in names
    assert "moneyflow.net_mf_amount" in names
    assert all("sql" not in item for item in result["data"])
    by_name = {item["field"]: item for item in result["data"]}
    assert by_name["market.amount"]["unit"] == "CNY"
    assert by_name["market.volume"]["unit"] == "share"
    assert by_name["market.pct_chg"]["unit"] == "percent"


@pytest.mark.asyncio
async def test_search_and_snapshot_generate_only_allowed_selects() -> None:
    search_query = FakeQuery([FakeResult(), FakeResult(), FakeResult()])
    search_result = await make_service(search_query).search_securities("银行")
    assert search_result["ok"] is True
    assert "unit_convention" not in search_result["meta"]
    assert len(search_query.statements) == 3

    snapshot_query = FakeQuery([FakeResult(scalar=date(2026, 7, 20)), FakeResult()])
    with pytest.raises(MarketQueryError) as error:
        await make_service(snapshot_query).get_stock_snapshot("000001.SZ")
    assert error.value.code == "NOT_FOUND"
    assert len(snapshot_query.statements) == 2


@pytest.mark.asyncio
async def test_all_stock_event_queries_pass_read_only_validator() -> None:
    fake = FakeQuery([FakeResult() for _ in range(9)])
    result = await make_service(fake).get_stock_events("000001.SZ")
    assert result["ok"] is True
    assert len(fake.statements) == 9
    sql = "\n".join(str(item.compile(dialect=postgresql.dialect())) for item in fake.statements)
    assert "stock_hot_rank_daily" in sql
    assert "stock_top_inst_daily" in sql
    assert "stock_limit_event_daily" in sql
    assert "market_theme_member_daily" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    ["hot", "limit", "limit_step", "top_list", "board_moneyflow", "market_theme"],
)
async def test_each_market_ranking_branch_is_a_bounded_select(kind: str) -> None:
    fake = FakeQuery([FakeResult(scalar=date(2026, 7, 20)), FakeResult()])
    result = await make_service(fake).get_market_rankings(cast(Any, kind), limit=999)
    assert result["ok"] is True
    assert result["meta"]["limit"] == 200
    assert len(fake.statements) == 2
    if kind == "limit":
        assert result["meta"]["raw_unit_status"].startswith("unknown")


@pytest.mark.asyncio
async def test_topic_branches_and_asset_branches_pass_validator() -> None:
    concept = FakeQuery(
        [
            FakeResult(rows=[{"source": "THS", "ts_code": "885001.TI", "name": "示例"}]),
            FakeResult(),
            FakeResult(scalar=date(2026, 7, 20)),
            FakeResult(),
        ]
    )
    concept_result = await make_service(concept).get_topic("concept", "示例")
    assert concept_result["ok"] is True
    assert concept_result["meta"]["member_basis"] == "current_snapshot"
    member_sql = str(concept.statements[1].compile(dialect=postgresql.dialect()))
    assert "concept_board_member.is_current IS true" in member_sql

    theme_index = FakeQuery(
        [
            FakeResult(rows=[{"source": "THS", "ts_code": "885900.TI", "name": "主题"}]),
            FakeResult(),
            FakeResult(scalar=date(2026, 7, 20)),
            FakeResult(),
        ]
    )
    theme_index_result = await make_service(theme_index).get_topic("theme_index", "主题")
    assert theme_index_result["ok"] is True
    assert theme_index_result["meta"]["member_basis"] == "current_snapshot"
    assert (
        theme_index_result["meta"]["units"]["daily.open/high/low/close/pre_close/avg_price/change"]
        == "index_point"
    )

    market_theme = FakeQuery(
        [
            FakeResult(scalar=date(2026, 7, 20)),
            FakeResult(
                rows=[
                    {
                        "source": "DC",
                        "theme_code": "BK001",
                        "trade_date": date(2026, 7, 20),
                        "name": "示例题材",
                    }
                ]
            ),
            FakeResult(),
        ]
    )
    theme_result = await make_service(market_theme).get_topic("market_theme", "示例题材")
    assert theme_result["ok"] is True

    etf = FakeQuery(
        [
            FakeResult(rows=[{"ts_code": "510300.SH", "csname": "沪深300ETF"}]),
            FakeResult(),
        ]
    )
    etf_result = await make_service(etf).get_index_or_etf("etf", "510300.SH", action="history")
    assert etf_result["ok"] is True

    index = FakeQuery(
        [
            FakeResult(rows=[{"ts_code": "000300.SH", "name": "沪深300"}]),
            FakeResult(scalar=date(2026, 7, 1)),
            FakeResult(scalar=date(2026, 6, 30)),
            FakeResult(),
        ]
    )
    index_result = await make_service(index).get_index_or_etf(
        "index", "000300.SH", action="constituents"
    )
    assert index_result["ok"] is True
    assert index_result["meta"]["snapshot_date"] == "2026-06-30"
    assert index_result["meta"]["units"]["profile.base_point"] == "index_point"


@pytest.mark.asyncio
async def test_data_status_accepts_only_known_published_datasets() -> None:
    fake = FakeQuery(
        [
            FakeResult(rows=[{"dataset_name": "stock_daily.core"}]),
            FakeResult(
                rows=[
                    {
                        "dataset_name": "stock_daily.core",
                        "scope_type": "DATE",
                        "business_date": date(2026, 7, 20),
                    }
                ]
            ),
        ]
    )
    result = await make_service(fake).get_data_status(["stock_daily.core"])
    assert result["ok"] is True
    assert result["data"][0]["business_date"] == "2026-07-20"


@pytest.mark.asyncio
async def test_topic_fuzzy_match_reports_ambiguity_instead_of_picking_first() -> None:
    fake = FakeQuery(
        [
            FakeResult(),
            FakeResult(
                rows=[
                    {"source": "THS", "ts_code": "885001.TI", "name": "机器人概念"},
                    {"source": "THS", "ts_code": "885002.TI", "name": "人形机器人"},
                ]
            ),
        ]
    )
    with pytest.raises(MarketQueryError) as error:
        await make_service(fake).get_topic("concept", "机器人")
    assert error.value.code == "AMBIGUOUS_IDENTIFIER"
    assert len(error.value.details["candidates"]) == 2


@pytest.mark.asyncio
async def test_screen_input_collections_are_bounded_before_query_building() -> None:
    fake = FakeQuery([])
    filters = [
        ScreenFilter(field="valuation.pb", operator=ScreenOperator.GT, value=index)
        for index in range(21)
    ]
    with pytest.raises(MarketQueryError) as error:
        await make_service(fake).screen_stocks(filters=filters)
    assert error.value.code == "INVALID_REQUEST"
    assert fake.statements == []

    with pytest.raises(ValidationError):
        ScreenFilter(
            field="stock.industry",
            operator=ScreenOperator.IN,
            value=[str(index) for index in range(101)],
        )

    with pytest.raises(MarketQueryError):
        await make_service(fake).screen_stocks(fields=["stock.name"] * 51)
    with pytest.raises(MarketQueryError):
        await make_service(fake).screen_stocks(
            sort=[ScreenSort(field="market.close") for _ in range(6)]
        )


@pytest.mark.asyncio
async def test_historical_topic_does_not_claim_current_members_as_historical() -> None:
    fake = FakeQuery(
        [
            FakeResult(rows=[{"source": "THS", "ts_code": "885001.TI", "name": "示例"}]),
            FakeResult(scalar=date(2025, 1, 15)),
            FakeResult(),
        ]
    )
    result = await make_service(fake).get_topic(
        "concept", "885001.TI", trade_date=date(2025, 1, 15)
    )
    assert result["meta"]["member_basis"] == "unavailable_for_historical_date"
    assert result["data"]["members"] == []
    assert all(
        "concept_board_member" not in str(statement.compile(dialect=postgresql.dialect()))
        for statement in fake.statements
    )
    assert (
        result["meta"]["units"]["daily.open/high/low/close/pre_close/avg_price/change"]
        == "index_point"
    )
