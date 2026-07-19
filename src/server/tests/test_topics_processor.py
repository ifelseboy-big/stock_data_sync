from datetime import date
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pyarrow as pa

from app.catalog import ApiSpec
from app.catalog.tushare import TOP_LIST_SPEC
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.topics import DatedRows, StockTopListDailyProcessor
from app.storage import LocalRawAssetStore, RawAssetContext

BUSINESS_DATE = date(2026, 7, 13)


def test_top_list_processor_deduplicates_identical_provider_rows(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    row = {
        "trade_date": "20260713",
        "ts_code": "603318.SH",
        "name": "示例股票",
        "close": 8.55,
        "pct_change": 10.04,
        "turnover_rate": 4.1,
        "amount": 87558849.0,
        "l_sell": None,
        "l_buy": 65798243.55,
        "l_amount": 65798243.55,
        "net_amount": 65798243.55,
        "net_rate": 75.15,
        "amount_rate": 75.15,
        "float_values": 1000000000.0,
        "reason": "融资买入数量达到总交易量的50%以上",
    }
    dependency = _asset(store, batch_id, TOP_LIST_SPEC, [row, row, row])

    prepared = StockTopListDailyProcessor().prepare(
        _task(batch_id),
        (dependency,),
        store,
    )

    payload = cast(DatedRows, prepared.payload)
    assert len(payload.rows) == 1
    assert prepared.rows_read == 3
    assert prepared.rows_rejected == 2


def _asset(
    store: LocalRawAssetStore,
    batch_id: UUID,
    spec: ApiSpec,
    rows: list[dict[str, object]],
) -> RawDependencyAsset:
    task_id = uuid4()
    metadata = store.seal(
        RawAssetContext(
            provider="TUSHARE",
            api_name=spec.api_name,
            business_date=BUSINESS_DATE,
            batch_id=batch_id,
            task_id=task_id,
        ),
        spec.schema,
        (pa.Table.from_pylist(rows, schema=spec.schema),),
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


def _task(batch_id: UUID) -> ClaimedProcessingTask:
    return ClaimedProcessingTask(
        process_id=uuid4(),
        source_batch_id=batch_id,
        process_type="stock_top_list_daily@1",
        business_date=BUSINESS_DATE,
        output_dataset="stock_top_list_daily",
        output_version=uuid4(),
        attempt_count=1,
        max_attempts=3,
    )
