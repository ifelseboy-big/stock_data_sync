from datetime import date

import pyarrow as pa
import pytest

from app.catalog import (
    ApiSpec,
    DatasetDependencySpec,
    DatasetSpec,
    DependencyKind,
    EmptyPolicy,
    QualityRuleSpec,
    ReleaseScope,
    RequestScope,
    RetryPolicy,
    ScheduleGroup,
    SpecRegistry,
    SplitPolicy,
    WriteStrategy,
)
from app.catalog.specs import ParameterValue


def _scope_builder(business_date: date | None) -> tuple[RequestScope, ...]:
    return (RequestScope("global", {"trade_date": business_date}),)


def _date_extractor(record: dict[str, object]) -> date | None:
    value = record.get("trade_date")
    return value if isinstance(value, date) else None


def _api_spec(fields: tuple[str, ...] = ("ts_code", "trade_date")) -> ApiSpec:
    return ApiSpec(
        api_name="daily",
        provider="TUSHARE",
        fields=fields,
        schema=pa.schema(tuple(pa.field(field, pa.string()) for field in fields)),
        natural_key=("ts_code",),
        schedule_group=ScheduleGroup.DAILY,
        scope_builder=_scope_builder,
        split_policy=SplitPolicy.TRADE_DATE,
        row_limit=6_000,
        empty_policy=EmptyPolicy.RETRY_UNTIL_CUTOFF,
        retry_policy=RetryPolicy(),
        date_extractor=_date_extractor,
    )


def test_api_spec_preserves_field_order_and_builds_scope() -> None:
    spec = _api_spec()

    assert spec.fields == ("ts_code", "trade_date")
    assert tuple(spec.scope_builder(date(2026, 7, 19)))[0].scope_key == "global"


def test_api_spec_rejects_duplicate_fields() -> None:
    with pytest.raises(ValueError, match="non-empty and unique"):
        _api_spec(("ts_code", "ts_code"))


def test_request_scope_copies_parameters() -> None:
    params: dict[str, ParameterValue] = {"exchange": "SSE"}
    scope = RequestScope("exchange=SSE", params)
    params["exchange"] = "SZSE"

    assert scope.params["exchange"] == "SSE"
    with pytest.raises(TypeError):
        scope.params["exchange"] = "BSE"  # type: ignore[index]


def test_dataset_spec_and_registry_reject_duplicate_registration() -> None:
    dependency = DatasetDependencySpec(
        kind=DependencyKind.RAW_ASSET,
        name="daily",
        scope=ReleaseScope.DATE,
    )
    spec = DatasetSpec(
        dataset_name="stock_daily.core",
        processor="stock_daily_core",
        processor_version="1",
        dependencies=(dependency,),
        write_strategy=WriteStrategy.REPLACE_DATE,
        release_scope=ReleaseScope.DATE,
        quality_rules=(QualityRuleSpec("natural_key_unique"),),
    )
    registry = SpecRegistry[DatasetSpec](lambda item: item.dataset_name)

    registry.register(spec)

    assert registry.get("stock_daily.core") is spec
    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec)
