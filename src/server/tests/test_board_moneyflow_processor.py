from datetime import date
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pyarrow as pa

from app.catalog import ApiSpec
from app.catalog.tushare import MONEYFLOW_CNT_THS_SPEC, MONEYFLOW_IND_THS_SPEC
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.board_moneyflow import (
    BoardMoneyflowRows,
    ThsBoardMoneyflowDailyProcessor,
)
from app.storage import LocalRawAssetStore, RawAssetContext

BUSINESS_DATE = date(2026, 5, 20)


def test_concept_moneyflow_accepts_missing_provider_code(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependencies = (
        _asset(
            store,
            batch_id,
            MONEYFLOW_CNT_THS_SPEC,
            [
                {
                    "trade_date": "20260520",
                    "ts_code": None,
                    "name": "AI视频",
                    "lead_stock": "示例股份",
                    "close_price": 10.0,
                    "pct_change": 2.5,
                    "industry_index": 1234.0,
                    "company_num": 20,
                    "pct_change_stock": 9.9,
                    "net_buy_amount": 200000000.0,
                    "net_sell_amount": 100000000.0,
                    "net_amount": 100000000.0,
                }
            ],
        ),
        _asset(store, batch_id, MONEYFLOW_IND_THS_SPEC, []),
    )

    prepared = ThsBoardMoneyflowDailyProcessor().prepare(
        _task(batch_id), dependencies, store
    )

    payload = cast(BoardMoneyflowRows, prepared.payload)
    assert len(payload.rows) == 1
    assert payload.rows[0]["board_name"] == "AI视频"
    assert payload.rows[0]["ts_code"] is None


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
        scope_key="trade_date=20260520",
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
        process_type="ths_board_moneyflow_daily@1",
        business_date=BUSINESS_DATE,
        output_dataset="ths_board_moneyflow_daily",
        output_version=uuid4(),
        attempt_count=1,
        max_attempts=3,
    )
