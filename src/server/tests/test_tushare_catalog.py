from datetime import date

from app.catalog import SplitPolicy
from app.catalog.datasets import ALL_DATASET_SPECS
from app.catalog.presentation import (
    DATASET_PRESENTATION_BY_NAME,
    TUSHARE_API_PRESENTATION_BY_NAME,
)
from app.catalog.tushare import (
    DAILY_FINAL_SPECS,
    DAILY_LATE_SPECS,
    DC_CONCEPT_CONS_SPEC,
    DC_CONCEPT_SPEC,
    DC_HOT_SPEC,
    DELAYED_ETF_SPECS,
    DELAYED_THEME_SPECS,
    ETF_BASIC_SPEC,
    ETF_SHARE_SIZE_SPEC,
    FUND_ADJ_SPEC,
    HOT_SPECS,
    INDEX_DAILY_SPEC,
    INDEX_WEIGHT_SPEC,
    MASTER_ENTITY_SPECS,
    MASTER_ETF_SPECS,
    MASTER_SPECIAL_SPECS,
    MASTER_STOCK_SPECS,
    MONEYFLOW_CNT_THS_SPEC,
    STK_FACTOR_SPEC,
    STK_LIMIT_SPEC,
    THS_INDEX_SPEC,
    TRADE_CAL_SPEC,
    build_tushare_api_registry,
    next_year_trade_calendar_scopes,
    ths_member_scopes,
)
from app.modules.operations.schemas import CreateBackfillCommand


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
        "ths_index",
        "ths_member",
        "ths_daily",
        "ths_hot",
        "dc_hot",
        "dc_concept",
        "dc_concept_cons",
        "top_list",
        "top_inst",
        "limit_list_d",
        "limit_step",
        "moneyflow_cnt_ths",
        "moneyflow_ind_ths",
        "index_basic",
        "index_daily",
        "index_dailybasic",
        "index_weight",
    }


def test_every_tushare_api_has_operator_facing_metadata() -> None:
    api_names = {spec.api_name for spec in build_tushare_api_registry().all()}

    assert set(TUSHARE_API_PRESENTATION_BY_NAME) == api_names
    assert all(item.display_name for item in TUSHARE_API_PRESENTATION_BY_NAME.values())
    assert all(item.description for item in TUSHARE_API_PRESENTATION_BY_NAME.values())


def test_every_dataset_has_operator_facing_metadata() -> None:
    dataset_names = {spec.dataset_name for spec in ALL_DATASET_SPECS}

    assert set(DATASET_PRESENTATION_BY_NAME) == dataset_names
    assert all(item.display_name for item in DATASET_PRESENTATION_BY_NAME.values())
    assert all(item.description for item in DATASET_PRESENTATION_BY_NAME.values())


def test_runtime_schedule_groups_remain_explicit() -> None:
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
        "ths_daily",
        "dc_concept",
        "top_list",
        "top_inst",
        "limit_list_d",
        "limit_step",
        "moneyflow_cnt_ths",
        "moneyflow_ind_ths",
        "index_daily",
        "index_dailybasic",
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


def test_special_catalog_uses_entity_splitting_and_date_pagination() -> None:
    assert {spec.api_name for spec in MASTER_SPECIAL_SPECS} == {
        "ths_index",
        "index_basic",
    }
    assert {spec.api_name for spec in MASTER_ENTITY_SPECS} == {"ths_member"}
    assert {spec.api_name for spec in DELAYED_THEME_SPECS} == {"dc_concept_cons"}
    assert {spec.api_name for spec in HOT_SPECS} == {"ths_hot", "dc_hot"}
    assert [scope.scope_key for scope in THS_INDEX_SPEC.scope_builder(None)] == [
        "exchange=A;type=N",
        "exchange=A;type=TH",
    ]
    assert [scope.scope_key for scope in ths_member_scopes(("885001.TI", "885002.TI"))] == [
        "ts_code=885001.TI",
        "ts_code=885002.TI",
    ]
    assert DC_CONCEPT_CONS_SPEC.split_policy == SplitPolicy.OFFSET
    assert DC_HOT_SPEC.natural_key == ()
    assert MONEYFLOW_CNT_THS_SPEC.natural_key == ("trade_date", "name")
    assert DC_CONCEPT_SPEC.historical_retention_months == 3
    assert [
        scope.scope_key for scope in DC_CONCEPT_CONS_SPEC.scope_builder(date(2026, 7, 17))
    ] == ["trade_date=20260717"]


def test_index_catalog_uses_configured_index_and_month_scopes() -> None:
    daily_scopes = tuple(INDEX_DAILY_SPEC.scope_builder(date(2026, 7, 17)))
    weight_scopes = tuple(INDEX_WEIGHT_SPEC.scope_builder(date(2026, 6, 1)))

    assert len(daily_scopes) == 6
    assert all(scope.params["trade_date"] == date(2026, 7, 17) for scope in daily_scopes)
    assert len(weight_scopes) == 6
    assert all(scope.params["start_date"] == date(2026, 6, 1) for scope in weight_scopes)
    assert all(scope.params["end_date"] == date(2026, 6, 30) for scope in weight_scopes)


def test_backfill_contract_accepts_the_complete_enabled_catalog() -> None:
    api_names = [spec.api_name for spec in build_tushare_api_registry().all()]

    command = CreateBackfillCommand(
        start_date=date(2026, 7, 13),
        end_date=date(2026, 7, 17),
        api_names=api_names,
        reason="完整接口历史回补",
    )

    assert command.api_names == api_names
