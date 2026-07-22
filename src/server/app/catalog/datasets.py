from app.catalog.specs import (
    DatasetDependencySpec,
    DatasetSpec,
    DependencyKind,
    QualityRuleSpec,
    ReleaseScope,
    SpecRegistry,
    WriteStrategy,
)


def build_dataset_registry() -> SpecRegistry[DatasetSpec]:
    registry = SpecRegistry[DatasetSpec](lambda spec: spec.dataset_name)
    for spec in ALL_DATASET_SPECS:
        registry.register(spec)
    return registry


TRADE_CALENDAR_DATASET = DatasetSpec(
    dataset_name="trade_calendar",
    processor="trade_calendar",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(
            kind=DependencyKind.RAW_ASSET,
            name="trade_cal",
            scope=ReleaseScope.GLOBAL,
        ),
    ),
    write_strategy=WriteStrategy.MASTER_MERGE,
    release_scope=ReleaseScope.GLOBAL,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("exchange", "cal_date")}),
        QualityRuleSpec("calendar_year_complete"),
    ),
)

STOCK_DATASET = DatasetSpec(
    dataset_name="stock",
    processor="stock",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(
            kind=DependencyKind.RAW_ASSET,
            name="stock_basic",
            scope=ReleaseScope.GLOBAL,
        ),
    ),
    write_strategy=WriteStrategy.MASTER_MERGE,
    release_scope=ReleaseScope.GLOBAL,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("ts_code",)}),
        QualityRuleSpec("stock_master_non_empty"),
    ),
)

STOCK_COMPANY_DATASET = DatasetSpec(
    dataset_name="stock_company",
    processor="stock_company",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(
            kind=DependencyKind.RAW_ASSET,
            name="stock_company",
            scope=ReleaseScope.GLOBAL,
        ),
        DatasetDependencySpec(
            kind=DependencyKind.DATASET_RELEASE,
            name="stock",
            scope=ReleaseScope.GLOBAL,
        ),
    ),
    write_strategy=WriteStrategy.MASTER_MERGE,
    release_scope=ReleaseScope.GLOBAL,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("ts_code",)}),
        QualityRuleSpec("filter_to_stock_master"),
    ),
)

ETF_DATASET = DatasetSpec(
    dataset_name="etf",
    processor="etf",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(
            kind=DependencyKind.RAW_ASSET,
            name="etf_basic",
            scope=ReleaseScope.GLOBAL,
        ),
    ),
    write_strategy=WriteStrategy.MASTER_MERGE,
    release_scope=ReleaseScope.GLOBAL,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("ts_code",)}),
        QualityRuleSpec("etf_master_non_empty"),
        QualityRuleSpec("etf_exchange_normalized"),
    ),
)

ETF_DAILY_DATASET = DatasetSpec(
    dataset_name="etf_daily",
    processor="etf_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "fund_daily", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "fund_adj", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "etf", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "trade_calendar",
            ReleaseScope.GLOBAL,
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("ts_code", "trade_date")}),
        QualityRuleSpec("filter_to_etf_master"),
        QualityRuleSpec("fund_adj_optional_for_daily_key"),
    ),
)

ETF_SHARE_SIZE_DAILY_DATASET = DatasetSpec(
    dataset_name="etf_share_size_daily",
    processor="etf_share_size_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(
            DependencyKind.RAW_ASSET,
            "etf_share_size",
            ReleaseScope.DATE,
        ),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "etf", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "trade_calendar",
            ReleaseScope.GLOBAL,
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("ts_code", "trade_date")}),
        QualityRuleSpec("filter_to_etf_master"),
        QualityRuleSpec("share_size_units_normalized"),
    ),
)

STOCK_DAILY_CORE_DATASET = DatasetSpec(
    dataset_name="stock_daily.core",
    processor="stock_daily_core",
    processor_version="4",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "daily", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "daily_basic", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "adj_factor", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "stock", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "trade_calendar",
            ReleaseScope.GLOBAL,
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("ts_code", "trade_date")}),
        QualityRuleSpec("daily_basic_enrichment_isolated_with_bse_coverage_compatibility"),
        QualityRuleSpec("adj_factor_covers_daily_keys"),
        QualityRuleSpec("daily_price_internal_consistency"),
    ),
)

STOCK_DAILY_LIMIT_DATASET = DatasetSpec(
    dataset_name="stock_daily.limit",
    processor="stock_daily_limit",
    processor_version="2",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "stk_limit", ReleaseScope.DATE),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "stock_daily.core",
            ReleaseScope.DATE,
        ),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "stock", ReleaseScope.GLOBAL),
    ),
    write_strategy=WriteStrategy.PATCH_COLUMNS,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec("patch_target_complete"),
        QualityRuleSpec("pre_close_consistent"),
    ),
)

STOCK_TECHNICAL_DAILY_DATASET = DatasetSpec(
    dataset_name="stock_technical_daily",
    processor="stock_technical_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "stk_factor", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "stock", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "stock_daily.core",
            ReleaseScope.DATE,
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("ts_code", "trade_date")}),
        QualityRuleSpec("overlapping_core_close_consistent"),
    ),
)

STOCK_MONEYFLOW_DAILY_DATASET = DatasetSpec(
    dataset_name="stock_moneyflow_daily",
    processor="stock_moneyflow_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "moneyflow", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "stock", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "trade_calendar",
            ReleaseScope.GLOBAL,
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("ts_code", "trade_date")}),
        QualityRuleSpec("stock_foreign_key_complete"),
    ),
)

STOCK_SUSPEND_DAILY_DATASET = DatasetSpec(
    dataset_name="stock_suspend_daily",
    processor="stock_suspend_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "suspend_d", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "stock", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "trade_calendar",
            ReleaseScope.GLOBAL,
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec(
            "natural_key_unique",
            {"columns": ("ts_code", "trade_date", "suspend_type")},
        ),
        QualityRuleSpec("empty_allowed"),
    ),
)

CONCEPT_BOARD_DATASET = DatasetSpec(
    dataset_name="concept_board",
    processor="concept_board",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "ths_index", ReleaseScope.GLOBAL),
    ),
    write_strategy=WriteStrategy.MASTER_MERGE,
    release_scope=ReleaseScope.GLOBAL,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("source", "ts_code")}),
        QualityRuleSpec("concept_board_non_empty"),
    ),
)

CONCEPT_BOARD_DAILY_DATASET = DatasetSpec(
    dataset_name="concept_board_daily",
    processor="concept_board_daily",
    processor_version="2",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "ths_daily", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "concept_board", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE, "trade_calendar", ReleaseScope.GLOBAL
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("source", "ts_code", "trade_date")}),
        QualityRuleSpec("filter_to_concept_board_master"),
    ),
)

CONCEPT_BOARD_MEMBER_DATASET = DatasetSpec(
    dataset_name="concept_board_member",
    processor="concept_board_member",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "ths_member", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "concept_board",
            ReleaseScope.GLOBAL,
            triggers_recompute=False,
        ),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "stock",
            ReleaseScope.GLOBAL,
            triggers_recompute=False,
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_ENTITY,
    release_scope=ReleaseScope.GLOBAL,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("source", "ts_code", "con_code")}),
        QualityRuleSpec("filter_to_current_stock_members"),
    ),
)

THEME_INDEX_DATASET = DatasetSpec(
    dataset_name="theme_index",
    processor="theme_index",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "ths_index", ReleaseScope.GLOBAL),
    ),
    write_strategy=WriteStrategy.MASTER_MERGE,
    release_scope=ReleaseScope.GLOBAL,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("source", "ts_code")}),
        QualityRuleSpec("theme_index_non_empty"),
    ),
)

THEME_INDEX_DAILY_DATASET = DatasetSpec(
    dataset_name="theme_index_daily",
    processor="theme_index_daily",
    processor_version="2",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "ths_daily", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "theme_index", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE, "trade_calendar", ReleaseScope.GLOBAL
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("source", "ts_code", "trade_date")}),
        QualityRuleSpec("filter_to_theme_index_master"),
    ),
)

THEME_INDEX_MEMBER_DATASET = DatasetSpec(
    dataset_name="theme_index_member",
    processor="theme_index_member",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "ths_member", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "theme_index",
            ReleaseScope.GLOBAL,
            triggers_recompute=False,
        ),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "stock",
            ReleaseScope.GLOBAL,
            triggers_recompute=False,
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_ENTITY,
    release_scope=ReleaseScope.GLOBAL,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("source", "ts_code", "con_code")}),
        QualityRuleSpec("filter_to_current_stock_members"),
    ),
)

STOCK_HOT_RANK_DAILY_DATASET = DatasetSpec(
    dataset_name="stock_hot_rank_daily",
    processor="stock_hot_rank_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "ths_hot", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "dc_hot", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "stock", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE, "trade_calendar", ReleaseScope.GLOBAL
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec(
            "natural_key_unique",
            {
                "columns": (
                    "source",
                    "trade_date",
                    "market_type",
                    "rank_type",
                    "ts_code",
                )
            },
        ),
        QualityRuleSpec("rank_positions_unique"),
        QualityRuleSpec("filter_to_stock_master"),
    ),
)

MARKET_THEME_DAILY_DATASET = DatasetSpec(
    dataset_name="market_theme_daily",
    processor="market_theme_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "dc_concept", ReleaseScope.DATE),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE, "trade_calendar", ReleaseScope.GLOBAL
        ),
    ),
    write_strategy=WriteStrategy.UPSERT_KEY,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("source", "theme_code", "trade_date")}),
    ),
)

MARKET_THEME_MEMBER_DAILY_DATASET = DatasetSpec(
    dataset_name="market_theme_member_daily",
    processor="market_theme_member_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(
            DependencyKind.RAW_ASSET,
            "dc_concept_cons",
            ReleaseScope.DATE,
            merge_previous_scopes=False,
        ),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE, "market_theme_daily", ReleaseScope.DATE
        ),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "stock", ReleaseScope.GLOBAL),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec(
            "natural_key_unique",
            {"columns": ("source", "trade_date", "theme_code", "ts_code")},
        ),
        QualityRuleSpec("filter_to_theme_and_stock_master"),
    ),
)

STOCK_TOP_LIST_DAILY_DATASET = DatasetSpec(
    dataset_name="stock_top_list_daily",
    processor="stock_top_list_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "top_list", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "stock", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE, "trade_calendar", ReleaseScope.GLOBAL
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(QualityRuleSpec("empty_allowed"),),
)

STOCK_TOP_INST_DAILY_DATASET = DatasetSpec(
    dataset_name="stock_top_inst_daily",
    processor="stock_top_inst_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "top_inst", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "stock", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE, "trade_calendar", ReleaseScope.GLOBAL
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(QualityRuleSpec("empty_allowed"),),
)

STOCK_LIMIT_EVENT_DAILY_DATASET = DatasetSpec(
    dataset_name="stock_limit_event_daily",
    processor="stock_limit_event_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "limit_list_d", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "stock", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE, "trade_calendar", ReleaseScope.GLOBAL
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(QualityRuleSpec("empty_allowed"),),
)

STOCK_LIMIT_STEP_DAILY_DATASET = DatasetSpec(
    dataset_name="stock_limit_step_daily",
    processor="stock_limit_step_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "limit_step", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "stock", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE, "trade_calendar", ReleaseScope.GLOBAL
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(QualityRuleSpec("empty_allowed"),),
)

THS_BOARD_MONEYFLOW_DAILY_DATASET = DatasetSpec(
    dataset_name="ths_board_moneyflow_daily",
    processor="ths_board_moneyflow_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "moneyflow_cnt_ths", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "moneyflow_ind_ths", ReleaseScope.DATE),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE, "trade_calendar", ReleaseScope.GLOBAL
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(
        QualityRuleSpec(
            "natural_key_unique",
            {"columns": ("board_type", "board_name", "trade_date")},
        ),
    ),
)

MARKET_INDEX_DATASET = DatasetSpec(
    dataset_name="market_index",
    processor="market_index",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "index_basic", ReleaseScope.GLOBAL),
    ),
    write_strategy=WriteStrategy.MASTER_MERGE,
    release_scope=ReleaseScope.GLOBAL,
    quality_rules=(
        QualityRuleSpec("natural_key_unique", {"columns": ("ts_code",)}),
        QualityRuleSpec("market_index_non_empty"),
    ),
)

MARKET_INDEX_DAILY_DATASET = DatasetSpec(
    dataset_name="market_index_daily",
    processor="market_index_daily",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "index_daily", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "market_index", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE, "trade_calendar", ReleaseScope.GLOBAL
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(QualityRuleSpec("natural_key_unique", {"columns": ("ts_code", "trade_date")}),),
)

INDEX_DAILY_BASIC_DATASET = DatasetSpec(
    dataset_name="index_daily_basic",
    processor="index_daily_basic",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "index_dailybasic", ReleaseScope.DATE),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "market_index", ReleaseScope.GLOBAL),
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE, "trade_calendar", ReleaseScope.GLOBAL
        ),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.DATE,
    quality_rules=(QualityRuleSpec("empty_allowed"),),
)

MARKET_INDEX_WEIGHT_DATASET = DatasetSpec(
    dataset_name="market_index_weight",
    processor="market_index_weight",
    processor_version="1",
    dependencies=(
        DatasetDependencySpec(DependencyKind.RAW_ASSET, "index_weight", ReleaseScope.MONTH),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "market_index", ReleaseScope.GLOBAL),
        DatasetDependencySpec(DependencyKind.DATASET_RELEASE, "stock", ReleaseScope.GLOBAL),
    ),
    write_strategy=WriteStrategy.REPLACE_DATE,
    release_scope=ReleaseScope.MONTH,
    quality_rules=(
        QualityRuleSpec(
            "natural_key_unique",
            {"columns": ("index_code", "snapshot_date", "con_code")},
        ),
        QualityRuleSpec("filter_to_index_and_stock_master"),
    ),
)

ALL_DATASET_SPECS = (
    TRADE_CALENDAR_DATASET,
    STOCK_DATASET,
    STOCK_COMPANY_DATASET,
    ETF_DATASET,
    ETF_DAILY_DATASET,
    ETF_SHARE_SIZE_DAILY_DATASET,
    STOCK_DAILY_CORE_DATASET,
    STOCK_DAILY_LIMIT_DATASET,
    STOCK_TECHNICAL_DAILY_DATASET,
    STOCK_MONEYFLOW_DAILY_DATASET,
    STOCK_SUSPEND_DAILY_DATASET,
    CONCEPT_BOARD_DATASET,
    CONCEPT_BOARD_DAILY_DATASET,
    CONCEPT_BOARD_MEMBER_DATASET,
    THEME_INDEX_DATASET,
    THEME_INDEX_DAILY_DATASET,
    THEME_INDEX_MEMBER_DATASET,
    STOCK_HOT_RANK_DAILY_DATASET,
    MARKET_THEME_DAILY_DATASET,
    MARKET_THEME_MEMBER_DAILY_DATASET,
    STOCK_TOP_LIST_DAILY_DATASET,
    STOCK_TOP_INST_DAILY_DATASET,
    STOCK_LIMIT_EVENT_DAILY_DATASET,
    STOCK_LIMIT_STEP_DAILY_DATASET,
    THS_BOARD_MONEYFLOW_DAILY_DATASET,
    MARKET_INDEX_DATASET,
    MARKET_INDEX_DAILY_DATASET,
    INDEX_DAILY_BASIC_DATASET,
    MARKET_INDEX_WEIGHT_DATASET,
)
