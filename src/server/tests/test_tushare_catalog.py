from datetime import date

from app.catalog import SplitPolicy
from app.catalog.tushare import (
    DAILY_FINAL_SPECS,
    DAILY_LATE_SPECS,
    MASTER_STOCK_SPECS,
    STK_FACTOR_SPEC,
    STK_LIMIT_SPEC,
    TRADE_CAL_SPEC,
    build_tushare_api_registry,
    next_year_trade_calendar_scopes,
)


def test_trade_calendar_is_split_by_exchange_and_separate_year_batches() -> None:
    scopes = tuple(TRADE_CAL_SPEC.scope_builder(date(2026, 7, 19)))

    assert [scope.scope_key for scope in scopes] == [
        "exchange=SSE;year=2026",
        "exchange=SZSE;year=2026",
    ]
    assert [scope.scope_key for scope in next_year_trade_calendar_scopes(date(2026, 7, 19))] == [
        "exchange=SSE;year=2027",
        "exchange=SZSE;year=2027",
    ]
    assert TRADE_CAL_SPEC.expected_row_count is not None
    assert TRADE_CAL_SPEC.expected_row_count(scopes[0].params) == 365


def test_activated_catalog_contains_core_stock_collection_interfaces() -> None:
    registry = build_tushare_api_registry()

    assert {spec.api_name for spec in registry.all()} == {
        "trade_cal",
        "stock_basic",
        "stock_company",
        "adj_factor",
        "daily",
        "daily_basic",
        "stk_limit",
        "moneyflow",
        "suspend_d",
        "stk_factor",
    }
    assert {spec.api_name for spec in MASTER_STOCK_SPECS} == {
        "stock_basic",
        "stock_company",
    }
    assert {spec.api_name for spec in DAILY_LATE_SPECS} == {
        "daily_basic",
        "stk_limit",
        "moneyflow",
        "suspend_d",
    }
    assert {spec.api_name for spec in DAILY_FINAL_SPECS} == {"stk_factor"}
    assert STK_LIMIT_SPEC.split_policy == SplitPolicy.OFFSET
    assert "turnover_rate" not in STK_FACTOR_SPEC.fields
