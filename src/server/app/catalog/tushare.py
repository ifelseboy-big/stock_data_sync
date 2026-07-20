from calendar import isleap
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, time

import pyarrow as pa

from app.catalog.specs import (
    ApiSpec,
    EmptyPolicy,
    RequestScope,
    RetryPolicy,
    ScheduleGroup,
    ScopeBuilder,
    SpecRegistry,
    SplitPolicy,
)
from app.core.config import settings


def build_tushare_api_registry() -> SpecRegistry[ApiSpec]:
    registry = SpecRegistry[ApiSpec](lambda spec: spec.api_name)
    for spec in ALL_TUSHARE_API_SPECS:
        registry.register(spec)
    return registry


def _trade_calendar_scopes(business_date: date | None) -> tuple[RequestScope, ...]:
    reference_date = business_date or date.today()
    return tuple(
        RequestScope(
            scope_key=f"exchange={exchange};year={year}",
            params={
                "exchange": exchange,
                "start_date": f"{year}0101",
                "end_date": f"{year}1231",
            },
        )
        for exchange in ("SSE", "SZSE")
        for year in (reference_date.year,)
    )


def next_year_trade_calendar_scopes(
    business_date: date | None,
) -> tuple[RequestScope, ...]:
    reference_date = business_date or date.today()
    next_year = reference_date.year + 1
    return tuple(
        RequestScope(
            scope_key=f"exchange={exchange};year={next_year}",
            params={
                "exchange": exchange,
                "start_date": f"{next_year}0101",
                "end_date": f"{next_year}1231",
            },
        )
        for exchange in ("SSE", "SZSE")
    )


def _calendar_expected_rows(params: Mapping[str, object]) -> int:
    year = int(str(params.get("start_date"))[:4])
    return 366 if isleap(year) else 365


def _global_scope(business_date: date | None) -> tuple[RequestScope, ...]:
    return (RequestScope("global", {}),)


def _trade_date_scope(business_date: date | None) -> tuple[RequestScope, ...]:
    if business_date is None:
        raise ValueError("trade-date API requires a business date")
    return (
        RequestScope(
            f"trade_date={business_date:%Y%m%d}",
            {"trade_date": business_date},
        ),
    )


def _stock_basic_scopes(business_date: date | None) -> tuple[RequestScope, ...]:
    return tuple(
        RequestScope(
            f"exchange={exchange};list_status={list_status}",
            {"exchange": exchange, "list_status": list_status},
        )
        for exchange in ("SSE", "SZSE", "BSE")
        for list_status in ("L", "D", "P", "G")
    )


def _stock_company_scopes(business_date: date | None) -> tuple[RequestScope, ...]:
    return tuple(
        RequestScope(f"exchange={exchange}", {"exchange": exchange})
        for exchange in ("SSE", "SZSE", "BSE")
    )


def _etf_basic_scopes(business_date: date | None) -> tuple[RequestScope, ...]:
    del business_date
    return tuple(
        RequestScope(
            f"list_status={list_status}",
            {"list_status": list_status},
        )
        for list_status in ("L", "D", "P")
    )


def _etf_share_size_scopes(business_date: date | None) -> tuple[RequestScope, ...]:
    if business_date is None:
        raise ValueError("ETF share-size API requires a business date")
    return tuple(
        RequestScope(
            f"trade_date={business_date:%Y%m%d};exchange={exchange}",
            {"trade_date": business_date, "exchange": exchange},
        )
        for exchange in ("SSE", "SZSE")
    )


def ths_member_scopes(codes: tuple[str, ...]) -> tuple[RequestScope, ...]:
    return tuple(RequestScope(f"ts_code={code}", {"ts_code": code}) for code in sorted(set(codes)))


def _dynamic_scopes_required(business_date: date | None) -> tuple[RequestScope, ...]:
    del business_date
    raise ValueError("this API requires entity scopes resolved from published master data")


def _index_basic_scopes(business_date: date | None) -> tuple[RequestScope, ...]:
    del business_date
    return tuple(
        RequestScope(f"market={market}", {"market": market})
        for market in ("SSE", "SZSE", "CSI", "MSCI", "CICC", "SW", "OTH")
    )


def _index_daily_scopes(business_date: date | None) -> tuple[RequestScope, ...]:
    if business_date is None:
        raise ValueError("index daily API requires a business date")
    return tuple(
        RequestScope(
            f"trade_date={business_date:%Y%m%d};ts_code={code}",
            {"trade_date": business_date, "ts_code": code},
        )
        for code in settings.market_index_codes
    )


def _index_weight_scopes(business_date: date | None) -> tuple[RequestScope, ...]:
    if business_date is None:
        raise ValueError("index weight API requires a target month")
    start_date = date(business_date.year, business_date.month, 1)
    month_index = start_date.year * 12 + start_date.month
    end_year, end_zero_month = divmod(month_index, 12)
    next_month = date(end_year, end_zero_month + 1, 1)
    end_date = date.fromordinal(next_month.toordinal() - 1)
    return tuple(
        RequestScope(
            f"month={start_date:%Y%m};index_code={code}",
            {
                "index_code": code,
                "start_date": start_date,
                "end_date": end_date,
            },
        )
        for code in settings.market_index_codes
    )


def _ths_hot_scopes(business_date: date | None) -> tuple[RequestScope, ...]:
    if business_date is None:
        raise ValueError("THS hot API requires a business date")
    return (
        RequestScope(
            f"trade_date={business_date:%Y%m%d};market=热股;is_new=Y",
            {"trade_date": business_date, "market": "热股", "is_new": "Y"},
        ),
    )


def _ths_hot_historical_scopes(business_date: date | None) -> tuple[RequestScope, ...]:
    if business_date is None:
        raise ValueError("THS hot API requires a business date")
    return (
        RequestScope(
            f"trade_date={business_date:%Y%m%d};market=热股;is_new=N",
            {"trade_date": business_date, "market": "热股", "is_new": "N"},
        ),
    )


def _dc_hot_scopes(business_date: date | None) -> tuple[RequestScope, ...]:
    if business_date is None:
        raise ValueError("DC hot API requires a business date")
    return tuple(
        RequestScope(
            f"trade_date={business_date:%Y%m%d};market=A股市场;hot_type={hot_type};is_new=Y",
            {
                "trade_date": business_date,
                "market": "A股市场",
                "hot_type": hot_type,
                "is_new": "Y",
            },
        )
        for hot_type in ("人气榜", "飙升榜")
    )


def _extract_trade_date(record: Mapping[str, object]) -> date | None:
    value = record.get("trade_date")
    if value is None:
        return None
    return datetime.strptime(str(value), "%Y%m%d").date()


def _arrow_schema(
    fields: tuple[str, ...],
    *,
    string_fields: frozenset[str],
    integer_fields: frozenset[str] = frozenset(),
) -> pa.Schema:
    return pa.schema(
        tuple(
            pa.field(
                field,
                pa.string()
                if field in string_fields
                else pa.int64()
                if field in integer_fields
                else pa.float64(),
            )
            for field in fields
        )
    )


def _master_spec(
    *,
    api_name: str,
    fields: tuple[str, ...],
    string_fields: frozenset[str],
    integer_fields: frozenset[str] = frozenset(),
    natural_key: tuple[str, ...],
    scope_builder: ScopeBuilder,
    empty_policy: EmptyPolicy = EmptyPolicy.FORBIDDEN,
) -> ApiSpec:
    return ApiSpec(
        api_name=api_name,
        provider="TUSHARE",
        fields=fields,
        schema=_arrow_schema(
            fields,
            string_fields=string_fields,
            integer_fields=integer_fields,
        ),
        natural_key=natural_key,
        schedule_group=ScheduleGroup.MASTER,
        scope_builder=scope_builder,
        split_policy=SplitPolicy.NONE,
        row_limit=10_000,
        empty_policy=empty_policy,
        retry_policy=RetryPolicy(max_attempts=3, initial_wait_seconds=60, max_wait_seconds=900),
        date_extractor=lambda record: None,
    )


def _daily_spec(
    *,
    api_name: str,
    fields: tuple[str, ...],
    string_fields: frozenset[str],
    integer_fields: frozenset[str] = frozenset(),
    natural_key: tuple[str, ...],
    row_limit: int,
    cutoff_time: time,
    empty_policy: EmptyPolicy = EmptyPolicy.RETRY_UNTIL_CUTOFF,
    split_policy: SplitPolicy = SplitPolicy.TRADE_DATE,
) -> ApiSpec:
    return ApiSpec(
        api_name=api_name,
        provider="TUSHARE",
        fields=fields,
        schema=_arrow_schema(
            fields,
            string_fields=string_fields,
            integer_fields=integer_fields,
        ),
        natural_key=natural_key,
        schedule_group=ScheduleGroup.DAILY,
        scope_builder=_trade_date_scope,
        split_policy=split_policy,
        row_limit=row_limit,
        empty_policy=empty_policy,
        retry_policy=RetryPolicy(
            max_attempts=5,
            initial_wait_seconds=120,
            max_wait_seconds=900,
            cutoff_time=cutoff_time,
        ),
        date_extractor=_extract_trade_date,
    )


TRADE_CAL_FIELDS = ("exchange", "cal_date", "is_open", "pretrade_date")
TRADE_CAL_SPEC = ApiSpec(
    api_name="trade_cal",
    provider="TUSHARE",
    fields=TRADE_CAL_FIELDS,
    schema=pa.schema(
        (
            pa.field("exchange", pa.string()),
            pa.field("cal_date", pa.string()),
            pa.field("is_open", pa.int64()),
            pa.field("pretrade_date", pa.string()),
        )
    ),
    natural_key=("exchange", "cal_date"),
    schedule_group=ScheduleGroup.MASTER,
    scope_builder=_trade_calendar_scopes,
    split_policy=SplitPolicy.NONE,
    row_limit=10_000,
    empty_policy=EmptyPolicy.RETRY_UNTIL_CUTOFF,
    retry_policy=RetryPolicy(max_attempts=3, initial_wait_seconds=60, max_wait_seconds=900),
    date_extractor=lambda record: None,
    endpoint_budget_per_minute=100,
    expected_row_count=_calendar_expected_rows,
)

STOCK_BASIC_FIELDS = (
    "ts_code",
    "symbol",
    "name",
    "area",
    "industry",
    "fullname",
    "enname",
    "cnspell",
    "market",
    "exchange",
    "curr_type",
    "list_status",
    "list_date",
    "delist_date",
    "is_hs",
    "act_name",
    "act_ent_type",
)
STOCK_BASIC_SPEC = _master_spec(
    api_name="stock_basic",
    fields=STOCK_BASIC_FIELDS,
    string_fields=frozenset(STOCK_BASIC_FIELDS),
    natural_key=("ts_code",),
    scope_builder=_stock_basic_scopes,
    empty_policy=EmptyPolicy.ALLOWED,
)

STOCK_COMPANY_FIELDS = (
    "ts_code",
    "com_name",
    "com_id",
    "exchange",
    "chairman",
    "manager",
    "secretary",
    "reg_capital",
    "setup_date",
    "province",
    "city",
    "introduction",
    "website",
    "email",
    "office",
    "employees",
    "main_business",
    "business_scope",
)
STOCK_COMPANY_SPEC = _master_spec(
    api_name="stock_company",
    fields=STOCK_COMPANY_FIELDS,
    string_fields=frozenset(STOCK_COMPANY_FIELDS) - {"reg_capital", "employees"},
    integer_fields=frozenset({"employees"}),
    natural_key=("ts_code",),
    scope_builder=_stock_company_scopes,
    empty_policy=EmptyPolicy.ALLOWED,
)

ETF_BASIC_FIELDS = (
    "ts_code",
    "csname",
    "extname",
    "cname",
    "index_code",
    "index_name",
    "setup_date",
    "list_date",
    "list_status",
    "exchange",
    "mgr_name",
    "custod_name",
    "mgt_fee",
    "etf_type",
)
ETF_BASIC_SPEC = _master_spec(
    api_name="etf_basic",
    fields=ETF_BASIC_FIELDS,
    string_fields=frozenset(ETF_BASIC_FIELDS) - {"mgt_fee"},
    natural_key=("ts_code",),
    scope_builder=_etf_basic_scopes,
    empty_policy=EmptyPolicy.ALLOWED,
)

DAILY_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
    "ah_vol",
    "ah_amount",
)
DAILY_SPEC = _daily_spec(
    api_name="daily",
    fields=DAILY_FIELDS,
    string_fields=frozenset({"ts_code", "trade_date"}),
    natural_key=("ts_code", "trade_date"),
    row_limit=6_000,
    cutoff_time=time(hour=20),
)

DAILY_BASIC_FIELDS = (
    "ts_code",
    "trade_date",
    "close",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)
DAILY_BASIC_SPEC = _daily_spec(
    api_name="daily_basic",
    fields=DAILY_BASIC_FIELDS,
    string_fields=frozenset({"ts_code", "trade_date"}),
    natural_key=("ts_code", "trade_date"),
    row_limit=6_000,
    cutoff_time=time(hour=22),
)

ADJ_FACTOR_FIELDS = ("ts_code", "trade_date", "adj_factor")
ADJ_FACTOR_SPEC = _daily_spec(
    api_name="adj_factor",
    fields=ADJ_FACTOR_FIELDS,
    string_fields=frozenset({"ts_code", "trade_date"}),
    natural_key=("ts_code", "trade_date"),
    row_limit=6_000,
    cutoff_time=time(hour=16),
)

STK_LIMIT_FIELDS = ("trade_date", "ts_code", "pre_close", "up_limit", "down_limit")
STK_LIMIT_SPEC = _daily_spec(
    api_name="stk_limit",
    fields=STK_LIMIT_FIELDS,
    string_fields=frozenset({"trade_date", "ts_code"}),
    natural_key=("ts_code", "trade_date"),
    row_limit=5_800,
    cutoff_time=time(hour=22),
    split_policy=SplitPolicy.OFFSET,
)

STK_FACTOR_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "open_hfq",
    "open_qfq",
    "high",
    "high_hfq",
    "high_qfq",
    "low",
    "low_hfq",
    "low_qfq",
    "close",
    "close_hfq",
    "close_qfq",
    "pre_close",
    "pre_close_hfq",
    "pre_close_qfq",
    "change",
    "pct_change",
    "vol",
    "amount",
    "adj_factor",
    "macd_dif",
    "macd_dea",
    "macd",
    "kdj_k",
    "kdj_d",
    "kdj_j",
    "rsi_6",
    "rsi_12",
    "rsi_24",
    "boll_upper",
    "boll_mid",
    "boll_lower",
    "cci",
)
STK_FACTOR_SPEC = _daily_spec(
    api_name="stk_factor",
    fields=STK_FACTOR_FIELDS,
    string_fields=frozenset({"ts_code", "trade_date"}),
    natural_key=("ts_code", "trade_date"),
    row_limit=10_000,
    cutoff_time=time(hour=23, minute=30),
)

MONEYFLOW_FIELDS = (
    "ts_code",
    "trade_date",
    "buy_sm_vol",
    "buy_sm_amount",
    "sell_sm_vol",
    "sell_sm_amount",
    "buy_md_vol",
    "buy_md_amount",
    "sell_md_vol",
    "sell_md_amount",
    "buy_lg_vol",
    "buy_lg_amount",
    "sell_lg_vol",
    "sell_lg_amount",
    "buy_elg_vol",
    "buy_elg_amount",
    "sell_elg_vol",
    "sell_elg_amount",
    "net_mf_vol",
    "net_mf_amount",
)
MONEYFLOW_SPEC = _daily_spec(
    api_name="moneyflow",
    fields=MONEYFLOW_FIELDS,
    string_fields=frozenset({"ts_code", "trade_date"}),
    natural_key=("ts_code", "trade_date"),
    row_limit=6_000,
    cutoff_time=time(hour=22),
)

SUSPEND_FIELDS = ("ts_code", "trade_date", "suspend_timing", "suspend_type")
SUSPEND_SPEC = _daily_spec(
    api_name="suspend_d",
    fields=SUSPEND_FIELDS,
    string_fields=frozenset(SUSPEND_FIELDS),
    natural_key=("ts_code", "trade_date", "suspend_type"),
    row_limit=5_000,
    cutoff_time=time(hour=22),
    empty_policy=EmptyPolicy.ALLOWED,
)

FUND_DAILY_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)
FUND_DAILY_SPEC = _daily_spec(
    api_name="fund_daily",
    fields=FUND_DAILY_FIELDS,
    string_fields=frozenset({"ts_code", "trade_date"}),
    natural_key=("ts_code", "trade_date"),
    row_limit=5_000,
    cutoff_time=time(hour=20),
)

FUND_ADJ_FIELDS = ("ts_code", "trade_date", "adj_factor")
FUND_ADJ_SPEC = _daily_spec(
    api_name="fund_adj",
    fields=FUND_ADJ_FIELDS,
    string_fields=frozenset({"ts_code", "trade_date"}),
    natural_key=("ts_code", "trade_date"),
    row_limit=2_000,
    cutoff_time=time(hour=23),
    split_policy=SplitPolicy.OFFSET,
)

ETF_SHARE_SIZE_FIELDS = (
    "ts_code",
    "trade_date",
    "etf_name",
    "total_share",
    "total_size",
    "nav",
    "close",
    "exchange",
)
ETF_SHARE_SIZE_SPEC = ApiSpec(
    api_name="etf_share_size",
    provider="TUSHARE",
    fields=ETF_SHARE_SIZE_FIELDS,
    schema=_arrow_schema(
        ETF_SHARE_SIZE_FIELDS,
        string_fields=frozenset({"ts_code", "trade_date", "etf_name", "exchange"}),
    ),
    natural_key=("ts_code", "trade_date"),
    schedule_group=ScheduleGroup.DELAYED,
    scope_builder=_etf_share_size_scopes,
    split_policy=SplitPolicy.NONE,
    row_limit=5_000,
    empty_policy=EmptyPolicy.RETRY_UNTIL_CUTOFF,
    retry_policy=RetryPolicy(
        max_attempts=5,
        initial_wait_seconds=900,
        max_wait_seconds=3_600,
    ),
    date_extractor=_extract_trade_date,
)

THS_INDEX_FIELDS = ("ts_code", "name", "count", "exchange", "list_date", "type")
THS_INDEX_SPEC = _master_spec(
    api_name="ths_index",
    fields=THS_INDEX_FIELDS,
    string_fields=frozenset(THS_INDEX_FIELDS) - {"count"},
    integer_fields=frozenset({"count"}),
    natural_key=("ts_code",),
    scope_builder=lambda business_date: (
        RequestScope("exchange=A;type=N", {"exchange": "A", "type": "N"}),
        RequestScope("exchange=A;type=TH", {"exchange": "A", "type": "TH"}),
    ),
)

THS_MEMBER_FIELDS = (
    "ts_code",
    "con_code",
    "con_name",
    "weight",
    "in_date",
    "out_date",
    "is_new",
)
THS_MEMBER_SPEC = ApiSpec(
    api_name="ths_member",
    provider="TUSHARE",
    fields=THS_MEMBER_FIELDS,
    schema=_arrow_schema(
        THS_MEMBER_FIELDS,
        string_fields=frozenset(THS_MEMBER_FIELDS) - {"weight"},
    ),
    natural_key=("ts_code", "con_code"),
    schedule_group=ScheduleGroup.MASTER,
    scope_builder=_dynamic_scopes_required,
    split_policy=SplitPolicy.BOARD,
    row_limit=5_000,
    empty_policy=EmptyPolicy.ALLOWED,
    retry_policy=RetryPolicy(max_attempts=3, initial_wait_seconds=60, max_wait_seconds=900),
    date_extractor=lambda record: None,
    endpoint_budget_per_minute=190,
)

THS_DAILY_FIELDS = (
    "ts_code",
    "trade_date",
    "close",
    "open",
    "high",
    "low",
    "pre_close",
    "avg_price",
    "change",
    "pct_change",
    "vol",
    "turnover_rate",
    "total_mv",
    "float_mv",
)
THS_DAILY_SPEC = _daily_spec(
    api_name="ths_daily",
    fields=THS_DAILY_FIELDS,
    string_fields=frozenset({"ts_code", "trade_date"}),
    natural_key=("ts_code", "trade_date"),
    row_limit=3_000,
    cutoff_time=time(hour=22, minute=30),
)

THS_HOT_FIELDS = (
    "trade_date",
    "data_type",
    "ts_code",
    "ts_name",
    "rank",
    "pct_change",
    "current_price",
    "concept",
    "rank_reason",
    "hot",
    "rank_time",
)
THS_HOT_SPEC = ApiSpec(
    api_name="ths_hot",
    provider="TUSHARE",
    fields=THS_HOT_FIELDS,
    schema=_arrow_schema(
        THS_HOT_FIELDS,
        string_fields=frozenset(
            {"trade_date", "data_type", "ts_code", "ts_name", "concept", "rank_reason", "rank_time"}
        ),
        integer_fields=frozenset({"rank"}),
    ),
    natural_key=("trade_date", "data_type", "ts_code", "rank_time"),
    schedule_group=ScheduleGroup.HOT,
    scope_builder=_ths_hot_scopes,
    historical_scope_builder=_ths_hot_historical_scopes,
    split_policy=SplitPolicy.OFFSET,
    row_limit=2_000,
    empty_policy=EmptyPolicy.RETRY_UNTIL_CUTOFF,
    retry_policy=RetryPolicy(
        max_attempts=5,
        initial_wait_seconds=120,
        max_wait_seconds=900,
        cutoff_time=time(hour=23),
    ),
    date_extractor=_extract_trade_date,
)

DC_HOT_FIELDS = (
    "trade_date",
    "data_type",
    "ts_code",
    "ts_name",
    "rank",
    "pct_change",
    "current_price",
    "hot",
    "concept",
    "rank_time",
)
DC_HOT_SPEC = ApiSpec(
    api_name="dc_hot",
    provider="TUSHARE",
    fields=DC_HOT_FIELDS,
    schema=_arrow_schema(
        DC_HOT_FIELDS,
        string_fields=frozenset(
            {"trade_date", "data_type", "ts_code", "ts_name", "concept", "rank_time"}
        ),
        integer_fields=frozenset({"rank"}),
    ),
    # Historical queries can return several legitimate rank_time snapshots even
    # with is_new=Y. Keep every provider row and select the latest coherent
    # snapshot in the processing layer.
    natural_key=(),
    schedule_group=ScheduleGroup.HOT,
    scope_builder=_dc_hot_scopes,
    split_policy=SplitPolicy.TRADE_DATE,
    row_limit=2_000,
    empty_policy=EmptyPolicy.RETRY_UNTIL_CUTOFF,
    retry_policy=RetryPolicy(
        max_attempts=5,
        initial_wait_seconds=120,
        max_wait_seconds=900,
        cutoff_time=time(hour=23),
    ),
    date_extractor=_extract_trade_date,
)

DC_CONCEPT_FIELDS = (
    "theme_code",
    "trade_date",
    "name",
    "pct_change",
    "hot",
    "sort",
    "strength",
    "z_t_num",
    "main_change",
    "lead_stock",
    "lead_stock_code",
    "lead_stock_pct_change",
)
DC_CONCEPT_SPEC = _daily_spec(
    api_name="dc_concept",
    fields=DC_CONCEPT_FIELDS,
    string_fields=frozenset({"theme_code", "trade_date", "name", "lead_stock", "lead_stock_code"}),
    integer_fields=frozenset({"sort", "z_t_num"}),
    natural_key=("theme_code", "trade_date"),
    row_limit=5_000,
    cutoff_time=time(hour=22, minute=30),
)
DC_CONCEPT_SPEC = replace(DC_CONCEPT_SPEC, historical_retention_months=3)

DC_CONCEPT_CONS_FIELDS = (
    "ts_code",
    "trade_date",
    "name",
    "theme_code",
    "industry_code",
    "industry",
    "reason",
    "hot_num",
)
DC_CONCEPT_CONS_SPEC = ApiSpec(
    api_name="dc_concept_cons",
    provider="TUSHARE",
    fields=DC_CONCEPT_CONS_FIELDS,
    schema=_arrow_schema(
        DC_CONCEPT_CONS_FIELDS,
        string_fields=frozenset(DC_CONCEPT_CONS_FIELDS) - {"hot_num"},
        integer_fields=frozenset({"hot_num"}),
    ),
    # The API occasionally returns byte-for-byte duplicate rows across OFFSET
    # pages. Preserve the raw response and deduplicate only during processing;
    # conflicting rows with the same business key still fail publication.
    natural_key=(),
    schedule_group=ScheduleGroup.DELAYED,
    scope_builder=_trade_date_scope,
    split_policy=SplitPolicy.OFFSET,
    row_limit=3_000,
    empty_policy=EmptyPolicy.ALLOWED,
    retry_policy=RetryPolicy(max_attempts=3, initial_wait_seconds=120, max_wait_seconds=900),
    date_extractor=_extract_trade_date,
)

TOP_LIST_FIELDS = (
    "trade_date",
    "ts_code",
    "name",
    "close",
    "pct_change",
    "turnover_rate",
    "amount",
    "l_sell",
    "l_buy",
    "l_amount",
    "net_amount",
    "net_rate",
    "amount_rate",
    "float_values",
    "reason",
)
TOP_LIST_SPEC = _daily_spec(
    api_name="top_list",
    fields=TOP_LIST_FIELDS,
    string_fields=frozenset({"trade_date", "ts_code", "name", "reason"}),
    # Tushare can return byte-for-byte duplicate top-list rows for the same
    # stock and reason. Keep the raw response intact and deduplicate only in
    # the processing layer, where conflicting duplicates can be distinguished.
    natural_key=(),
    row_limit=10_000,
    cutoff_time=time(hour=22, minute=30),
    empty_policy=EmptyPolicy.ALLOWED,
)

TOP_INST_FIELDS = (
    "trade_date",
    "ts_code",
    "exalter",
    "side",
    "buy",
    "buy_rate",
    "sell",
    "sell_rate",
    "net_buy",
    "reason",
)
TOP_INST_SPEC = _daily_spec(
    api_name="top_inst",
    fields=TOP_INST_FIELDS,
    string_fields=frozenset({"trade_date", "ts_code", "exalter", "reason"}),
    integer_fields=frozenset({"side"}),
    natural_key=(),
    row_limit=10_000,
    cutoff_time=time(hour=22, minute=30),
    empty_policy=EmptyPolicy.ALLOWED,
)

LIMIT_LIST_FIELDS = (
    "trade_date",
    "ts_code",
    "industry",
    "name",
    "close",
    "pct_chg",
    "amount",
    "limit_amount",
    "float_mv",
    "total_mv",
    "turnover_ratio",
    "fd_amount",
    "first_time",
    "last_time",
    "open_times",
    "up_stat",
    "limit_times",
    "limit",
)
LIMIT_LIST_SPEC = _daily_spec(
    api_name="limit_list_d",
    fields=LIMIT_LIST_FIELDS,
    string_fields=frozenset(
        {"trade_date", "ts_code", "industry", "name", "first_time", "last_time", "up_stat", "limit"}
    ),
    integer_fields=frozenset({"open_times", "limit_times"}),
    natural_key=("trade_date", "ts_code", "limit"),
    row_limit=2_500,
    cutoff_time=time(hour=22, minute=30),
    split_policy=SplitPolicy.OFFSET,
)

LIMIT_STEP_FIELDS = ("ts_code", "name", "trade_date", "nums")
LIMIT_STEP_SPEC = _daily_spec(
    api_name="limit_step",
    fields=LIMIT_STEP_FIELDS,
    string_fields=frozenset({"ts_code", "name", "trade_date"}),
    integer_fields=frozenset({"nums"}),
    natural_key=("trade_date", "ts_code"),
    row_limit=2_000,
    cutoff_time=time(hour=22, minute=30),
    empty_policy=EmptyPolicy.ALLOWED,
)

MONEYFLOW_CNT_THS_FIELDS = (
    "trade_date",
    "ts_code",
    "name",
    "lead_stock",
    "close_price",
    "pct_change",
    "industry_index",
    "company_num",
    "pct_change_stock",
    "net_buy_amount",
    "net_sell_amount",
    "net_amount",
)
MONEYFLOW_CNT_THS_SPEC = _daily_spec(
    api_name="moneyflow_cnt_ths",
    fields=MONEYFLOW_CNT_THS_FIELDS,
    string_fields=frozenset({"trade_date", "ts_code", "name", "lead_stock"}),
    integer_fields=frozenset({"company_num"}),
    # The provider currently returns a stable concept name but no ts_code for
    # some boards (for example AI视频). The daily name remains unique.
    natural_key=("trade_date", "name"),
    row_limit=5_000,
    cutoff_time=time(hour=22, minute=30),
)

MONEYFLOW_IND_THS_FIELDS = (
    "trade_date",
    "ts_code",
    "industry",
    "lead_stock",
    "close",
    "pct_change",
    "company_num",
    "pct_change_stock",
    "close_price",
    "net_buy_amount",
    "net_sell_amount",
    "net_amount",
)
MONEYFLOW_IND_THS_SPEC = _daily_spec(
    api_name="moneyflow_ind_ths",
    fields=MONEYFLOW_IND_THS_FIELDS,
    string_fields=frozenset({"trade_date", "ts_code", "industry", "lead_stock"}),
    integer_fields=frozenset({"company_num"}),
    natural_key=("trade_date", "ts_code"),
    row_limit=5_000,
    cutoff_time=time(hour=22, minute=30),
)

INDEX_BASIC_FIELDS = (
    "ts_code",
    "name",
    "fullname",
    "market",
    "publisher",
    "index_type",
    "category",
    "base_date",
    "base_point",
    "list_date",
    "weight_rule",
    "desc",
    "exp_date",
)
INDEX_BASIC_SPEC = ApiSpec(
    api_name="index_basic",
    provider="TUSHARE",
    fields=INDEX_BASIC_FIELDS,
    schema=_arrow_schema(
        INDEX_BASIC_FIELDS,
        string_fields=frozenset(INDEX_BASIC_FIELDS) - {"base_point"},
    ),
    natural_key=("ts_code",),
    schedule_group=ScheduleGroup.MASTER,
    scope_builder=_index_basic_scopes,
    split_policy=SplitPolicy.OFFSET,
    row_limit=8_000,
    empty_policy=EmptyPolicy.ALLOWED,
    retry_policy=RetryPolicy(max_attempts=3, initial_wait_seconds=60, max_wait_seconds=900),
    date_extractor=lambda record: None,
)

INDEX_DAILY_FIELDS = (
    "ts_code",
    "trade_date",
    "close",
    "open",
    "high",
    "low",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)
INDEX_DAILY_SPEC = ApiSpec(
    api_name="index_daily",
    provider="TUSHARE",
    fields=INDEX_DAILY_FIELDS,
    schema=_arrow_schema(
        INDEX_DAILY_FIELDS,
        string_fields=frozenset({"ts_code", "trade_date"}),
    ),
    natural_key=("ts_code", "trade_date"),
    schedule_group=ScheduleGroup.DAILY,
    scope_builder=_index_daily_scopes,
    split_policy=SplitPolicy.INDEX,
    row_limit=1_000,
    empty_policy=EmptyPolicy.RETRY_UNTIL_CUTOFF,
    retry_policy=RetryPolicy(
        max_attempts=5,
        initial_wait_seconds=120,
        max_wait_seconds=900,
        cutoff_time=time(hour=22, minute=30),
    ),
    date_extractor=_extract_trade_date,
)

INDEX_DAILY_BASIC_FIELDS = (
    "ts_code",
    "trade_date",
    "total_mv",
    "float_mv",
    "total_share",
    "float_share",
    "free_share",
    "turnover_rate",
    "turnover_rate_f",
    "pe",
    "pe_ttm",
    "pb",
)
INDEX_DAILY_BASIC_SPEC = _daily_spec(
    api_name="index_dailybasic",
    fields=INDEX_DAILY_BASIC_FIELDS,
    string_fields=frozenset({"ts_code", "trade_date"}),
    natural_key=("ts_code", "trade_date"),
    row_limit=3_000,
    cutoff_time=time(hour=22, minute=30),
)

INDEX_WEIGHT_FIELDS = ("index_code", "con_code", "trade_date", "weight")
INDEX_WEIGHT_SPEC = ApiSpec(
    api_name="index_weight",
    provider="TUSHARE",
    fields=INDEX_WEIGHT_FIELDS,
    schema=_arrow_schema(
        INDEX_WEIGHT_FIELDS,
        string_fields=frozenset({"index_code", "con_code", "trade_date"}),
    ),
    natural_key=("index_code", "trade_date", "con_code"),
    schedule_group=ScheduleGroup.MASTER,
    scope_builder=_index_weight_scopes,
    split_policy=SplitPolicy.MONTH,
    row_limit=5_000,
    empty_policy=EmptyPolicy.ALLOWED,
    retry_policy=RetryPolicy(max_attempts=3, initial_wait_seconds=120, max_wait_seconds=900),
    date_extractor=lambda record: None,
)

MASTER_STOCK_SPECS = (STOCK_BASIC_SPEC, STOCK_COMPANY_SPEC)
MASTER_ETF_SPECS = (ETF_BASIC_SPEC,)
MASTER_SPECIAL_SPECS = (THS_INDEX_SPEC, INDEX_BASIC_SPEC)
MASTER_ENTITY_SPECS = (THS_MEMBER_SPEC,)
DAILY_PREOPEN_SPECS = (ADJ_FACTOR_SPEC,)
DAILY_CLOSE_SPECS = (DAILY_SPEC, FUND_DAILY_SPEC)
DAILY_LATE_SPECS = (
    DAILY_BASIC_SPEC,
    STK_LIMIT_SPEC,
    MONEYFLOW_SPEC,
    SUSPEND_SPEC,
    FUND_ADJ_SPEC,
    THS_DAILY_SPEC,
    DC_CONCEPT_SPEC,
    TOP_LIST_SPEC,
    TOP_INST_SPEC,
    LIMIT_LIST_SPEC,
    LIMIT_STEP_SPEC,
    MONEYFLOW_CNT_THS_SPEC,
    MONEYFLOW_IND_THS_SPEC,
    INDEX_DAILY_SPEC,
    INDEX_DAILY_BASIC_SPEC,
)
DAILY_FINAL_SPECS = (STK_FACTOR_SPEC,)
DELAYED_ETF_SPECS = (ETF_SHARE_SIZE_SPEC,)
DELAYED_THEME_SPECS = (DC_CONCEPT_CONS_SPEC,)
HOT_SPECS = (THS_HOT_SPEC, DC_HOT_SPEC)
MONTHLY_INDEX_SPECS = (INDEX_WEIGHT_SPEC,)
ALL_TUSHARE_API_SPECS = (
    TRADE_CAL_SPEC,
    *MASTER_STOCK_SPECS,
    *MASTER_ETF_SPECS,
    *MASTER_SPECIAL_SPECS,
    *MASTER_ENTITY_SPECS,
    *DAILY_PREOPEN_SPECS,
    *DAILY_CLOSE_SPECS,
    *DAILY_LATE_SPECS,
    *DAILY_FINAL_SPECS,
    *DELAYED_ETF_SPECS,
    *DELAYED_THEME_SPECS,
    *HOT_SPECS,
    *MONTHLY_INDEX_SPECS,
)
