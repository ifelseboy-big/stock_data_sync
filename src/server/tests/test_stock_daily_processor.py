from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pyarrow as pa
import pytest

from app.catalog import ApiSpec
from app.catalog.bse_codes import BSE_CODE_ALIASES, canonical_stock_code
from app.catalog.tushare import (
    ADJ_FACTOR_SPEC,
    DAILY_BASIC_SPEC,
    DAILY_SPEC,
    STK_FACTOR_SPEC,
    SUSPEND_SPEC,
)
from app.common.errors import ProcessingError
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.stock_daily import (
    StockDailyCoreProcessor,
    StockSuspendDailyProcessor,
    StockTechnicalDailyProcessor,
)
from app.modules.processing.staging import PreparedRow
from app.storage import LocalRawAssetStore, RawAssetContext

BUSINESS_DATE = date(2026, 7, 17)


def test_stock_daily_core_uses_daily_keys_and_allows_extra_factor_rows(
    tmp_path: Path,
) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependencies = (
        _asset(
            store,
            batch_id,
            DAILY_SPEC,
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260717",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.5,
                    "close": 10.5,
                    "pre_close": 10.0,
                    "change": 0.5,
                    "pct_chg": 5.0,
                    "vol": 10.0,
                    "amount": 20.0,
                }
            ],
        ),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260717",
                    "close": 10.5,
                }
            ],
        ),
        _asset(
            store,
            batch_id,
            ADJ_FACTOR_SPEC,
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260717",
                    "adj_factor": 1.25,
                },
                {
                    "ts_code": "000002.SZ",
                    "trade_date": "20260717",
                    "adj_factor": 1.5,
                },
            ],
        ),
    )

    prepared = StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert len(rows) == 1
    assert prepared.rows_read == 4
    assert prepared.rows_rejected == 1
    assert rows[0]["volume"] == Decimal("1000.0")
    assert rows[0]["amount"] == Decimal("20000.0")


def test_bse_code_mapping_uses_complete_official_table() -> None:
    assert len(BSE_CODE_ALIASES) == 248
    assert len(set(BSE_CODE_ALIASES.values())) == 248
    assert canonical_stock_code("831396.BJ") == "920496.BJ"
    assert canonical_stock_code("836961.BJ") == "920061.BJ"
    assert canonical_stock_code("000001.SZ") == "000001.SZ"


def test_stock_technical_maps_bse_alias_and_prefers_current_code(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependencies = (
        _asset(
            store,
            batch_id,
            STK_FACTOR_SPEC,
            [
                _technical_source("430017.BJ", close=10.5, macd=1.0),
                _technical_source("920017.BJ", close=10.5, macd=2.0),
            ],
        ),
    )

    prepared = StockTechnicalDailyProcessor().prepare(
        _task(batch_id), dependencies, store
    )

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert len(rows) == 1
    assert rows[0]["ts_code"] == "920017.BJ"
    assert rows[0]["macd"] == Decimal("2.0")
    assert prepared.rows_rejected == 1
    assert "430017.BJ->920017.BJ" in prepared.warning_messages[0]


def test_stock_technical_rejects_conflicting_bse_alias_rows(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependencies = (
        _asset(
            store,
            batch_id,
            STK_FACTOR_SPEC,
            [
                _technical_source("430017.BJ", close=10.5, macd=1.0),
                _technical_source("920017.BJ", close=10.6, macd=2.0),
            ],
        ),
    )

    with pytest.raises(ProcessingError, match="BSE code alias conflict"):
        StockTechnicalDailyProcessor().prepare(_task(batch_id), dependencies, store)


def test_stock_suspend_maps_old_only_bse_code_without_rejecting_it(
    tmp_path: Path,
) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependencies = (
        _asset(
            store,
            batch_id,
            SUSPEND_SPEC,
            [
                {
                    "ts_code": "831961.BJ",
                    "trade_date": "20260717",
                    "suspend_type": "S",
                    "suspend_timing": "09:30:00",
                }
            ],
        ),
    )

    prepared = StockSuspendDailyProcessor().prepare(_task(batch_id), dependencies, store)

    _, rows = cast(tuple[date, tuple[PreparedRow, ...]], prepared.payload)
    assert rows[0]["ts_code"] == "920961.BJ"
    assert prepared.rows_rejected == 0
    assert "831961.BJ->920961.BJ" in prepared.warning_messages[0]


def _asset(
    store: LocalRawAssetStore,
    batch_id: UUID,
    spec: ApiSpec,
    rows: list[dict[str, object]],
) -> RawDependencyAsset:
    task_id = uuid4()
    table = pa.Table.from_pylist(rows, schema=spec.schema)
    metadata = store.seal(
        RawAssetContext(
            provider="TUSHARE",
            api_name=spec.api_name,
            business_date=BUSINESS_DATE,
            batch_id=batch_id,
            task_id=task_id,
        ),
        spec.schema,
        (table,),
    )
    return RawDependencyAsset(
        dependency_name=spec.api_name,
        scope_key=f"trade_date={BUSINESS_DATE:%Y%m%d}",
        asset_id=uuid4(),
        storage_uri=metadata.storage_uri,
        content_hash=metadata.content_hash,
        schema_fingerprint=metadata.schema_fingerprint,
        row_count=metadata.row_count,
    )


def _technical_source(ts_code: str, *, close: float, macd: float) -> dict[str, object]:
    return {
        "ts_code": ts_code,
        "trade_date": "20260717",
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": close,
        "pre_close": 10.0,
        "change": close - 10.0,
        "pct_change": (close - 10.0) * 10,
        "vol": 10.0,
        "amount": 20.0,
        "macd": macd,
    }


def _task(batch_id: UUID) -> ClaimedProcessingTask:
    return ClaimedProcessingTask(
        process_id=uuid4(),
        source_batch_id=batch_id,
        process_type="stock_daily_core@1",
        business_date=BUSINESS_DATE,
        output_dataset="stock_daily.core",
        output_version=uuid4(),
        attempt_count=1,
        max_attempts=3,
    )
