from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pyarrow as pa

from app.catalog import ApiSpec
from app.catalog.tushare import (
    ETF_BASIC_SPEC,
    ETF_SHARE_SIZE_SPEC,
    FUND_ADJ_SPEC,
    FUND_DAILY_SPEC,
)
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.etf import (
    EtfDailyProcessor,
    EtfProcessor,
    EtfShareSizeDailyProcessor,
)
from app.modules.processing.staging import PreparedRow
from app.storage import LocalRawAssetStore, RawAssetContext

BUSINESS_DATE = date(2026, 7, 17)


def test_etf_master_normalizes_exchange_and_status(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependencies = (
        _asset(
            store,
            batch_id,
            ETF_BASIC_SPEC,
            [
                {
                    "ts_code": "510300.SH",
                    "csname": "300ETF",
                    "extname": "沪深300ETF",
                    "cname": "沪深300交易型开放式指数基金",
                    "index_code": "000300.SH",
                    "index_name": "沪深300指数",
                    "setup_date": "20120504",
                    "list_date": "20120528",
                    "list_status": "L",
                    "exchange": "SH",
                    "mgr_name": "基金管理人",
                    "custod_name": "基金托管人",
                    "mgt_fee": 0.5,
                    "etf_type": "境内",
                }
            ],
            business_date=None,
        ),
    )

    prepared = EtfProcessor().prepare(_task(batch_id, "etf"), dependencies, store)

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert rows[0]["exchange"] == "SSE"
    assert rows[0]["source_exchange"] == "SH"
    assert rows[0]["list_status"] == "L"
    assert rows[0]["mgt_fee"] == Decimal("0.5")


def test_etf_daily_joins_factor_and_rejects_non_daily_factor_rows(
    tmp_path: Path,
) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependencies = (
        _asset(
            store,
            batch_id,
            FUND_DAILY_SPEC,
            [
                {
                    "ts_code": "510300.SH",
                    "trade_date": "20260717",
                    "open": 4.0,
                    "high": 4.1,
                    "low": 3.9,
                    "close": 4.05,
                    "pre_close": 4.0,
                    "change": 0.05,
                    "pct_chg": 1.25,
                    "vol": 100.0,
                    "amount": 200.0,
                }
            ],
        ),
        _asset(
            store,
            batch_id,
            FUND_ADJ_SPEC,
            [
                {
                    "ts_code": "510300.SH",
                    "trade_date": "20260717",
                    "adj_factor": 1.2,
                },
                {
                    "ts_code": "000001.OF",
                    "trade_date": "20260717",
                    "adj_factor": 1.0,
                },
            ],
        ),
    )

    prepared = EtfDailyProcessor().prepare(
        _task(batch_id, "etf_daily"),
        dependencies,
        store,
    )

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert len(rows) == 1
    assert prepared.rows_read == 3
    assert prepared.rows_rejected == 1
    assert rows[0]["volume"] == Decimal("10000.0")
    assert rows[0]["amount"] == Decimal("200000.0")
    assert rows[0]["adj_factor"] == Decimal("1.2")


def test_etf_share_size_normalizes_units(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependencies = (
        _asset(
            store,
            batch_id,
            ETF_SHARE_SIZE_SPEC,
            [
                {
                    "ts_code": "510300.SH",
                    "trade_date": "20260717",
                    "etf_name": "300ETF",
                    "total_share": 100.5,
                    "total_size": 402.0,
                    "nav": 4.0,
                    "close": 4.05,
                    "exchange": "SSE",
                },
                {
                    "ts_code": "513100.SH",
                    "trade_date": "20260717",
                    "etf_name": "海外ETF",
                    "total_share": 50.0,
                    "total_size": None,
                    "nav": None,
                    "close": 2.0,
                    "exchange": "SSE",
                },
            ],
        ),
    )

    prepared = EtfShareSizeDailyProcessor().prepare(
        _task(batch_id, "etf_share_size_daily"),
        dependencies,
        store,
    )

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert rows[0]["total_share"] == Decimal("1005000.0")
    assert rows[0]["total_size"] == Decimal("4020000.0")
    assert rows[0]["exchange"] == "SSE"
    assert prepared.rows_read == 2
    assert prepared.rows_rejected == 1


def _asset(
    store: LocalRawAssetStore,
    batch_id: UUID,
    spec: ApiSpec,
    rows: list[dict[str, object]],
    *,
    business_date: date | None = BUSINESS_DATE,
) -> RawDependencyAsset:
    task_id = uuid4()
    table = pa.Table.from_pylist(rows, schema=spec.schema)
    metadata = store.seal(
        RawAssetContext(
            provider="TUSHARE",
            api_name=spec.api_name,
            business_date=business_date,
            batch_id=batch_id,
            task_id=task_id,
        ),
        spec.schema,
        (table,),
    )
    return RawDependencyAsset(
        dependency_name=spec.api_name,
        scope_key="test",
        asset_id=uuid4(),
        storage_uri=metadata.storage_uri,
        content_hash=metadata.content_hash,
        schema_fingerprint=metadata.schema_fingerprint,
        row_count=metadata.row_count,
    )


def _task(batch_id: UUID, dataset: str) -> ClaimedProcessingTask:
    return ClaimedProcessingTask(
        process_id=uuid4(),
        source_batch_id=batch_id,
        process_type=f"{dataset}@1",
        business_date=BUSINESS_DATE,
        output_dataset=dataset,
        output_version=uuid4(),
        attempt_count=1,
        max_attempts=3,
    )
