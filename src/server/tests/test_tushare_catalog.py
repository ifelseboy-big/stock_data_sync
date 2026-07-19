from datetime import date

from app.catalog import SplitPolicy
from app.catalog.tushare import (
    DAILY_FINAL_SPECS,
    DAILY_LATE_SPECS,
    DELAYED_ETF_SPECS,
    ETF_BASIC_SPEC,
    ETF_SHARE_SIZE_SPEC,
    FUND_ADJ_SPEC,
    MASTER_ETF_SPECS,
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
        "etf_basic",
        "fund_daily",
        "fund_adj",
        "etf_share_size",
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
        "fund_adj",
    }
    assert {spec.api_name for spec in DAILY_FINAL_SPECS} == {"stk_factor"}
    assert STK_LIMIT_SPEC.split_policy == SplitPolicy.OFFSET
    assert FUND_ADJ_SPEC.split_policy == SplitPolicy.OFFSET
    assert "turnover_rate" not in STK_FACTOR_SPEC.fields


def test_etf_catalog_uses_status_and_exchange_scopes() -> None:
    assert {spec.api_name for spec in MASTER_ETF_SPECS} == {"etf_basic"}
    assert [scope.scope_key for scope in ETF_BASIC_SPEC.scope_builder(None)] == [
        "list_status=L",
        "list_status=D",
        "list_status=P",
    ]
    assert {spec.api_name for spec in DELAYED_ETF_SPECS} == {"etf_share_size"}
    assert [scope.scope_key for scope in ETF_SHARE_SIZE_SPEC.scope_builder(date(2026, 7, 17))] == [
        "trade_date=20260717;exchange=SSE",
        "trade_date=20260717;exchange=SZSE",
    ]
