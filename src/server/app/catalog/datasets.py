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

STOCK_DAILY_CORE_DATASET = DatasetSpec(
    dataset_name="stock_daily.core",
    processor="stock_daily_core",
    processor_version="1",
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
        QualityRuleSpec("daily_and_daily_basic_keys_equal"),
        QualityRuleSpec("adj_factor_covers_daily_keys"),
        QualityRuleSpec("daily_close_consistent"),
    ),
)

STOCK_DAILY_LIMIT_DATASET = DatasetSpec(
    dataset_name="stock_daily.limit",
    processor="stock_daily_limit",
    processor_version="1",
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

ALL_DATASET_SPECS = (
    TRADE_CALENDAR_DATASET,
    STOCK_DATASET,
    STOCK_COMPANY_DATASET,
    STOCK_DAILY_CORE_DATASET,
    STOCK_DAILY_LIMIT_DATASET,
    STOCK_TECHNICAL_DAILY_DATASET,
    STOCK_MONEYFLOW_DAILY_DATASET,
    STOCK_SUSPEND_DAILY_DATASET,
)
