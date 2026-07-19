from calendar import isleap
from datetime import date
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pytest

from app.catalog.tushare import TRADE_CAL_SPEC
from app.common.errors import ProcessingError
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.trade_calendar import TradeCalendarProcessor
from app.storage import LocalRawAssetStore, RawAssetContext


def _task() -> ClaimedProcessingTask:
    return ClaimedProcessingTask(
        process_id=uuid4(),
        source_batch_id=uuid4(),
        process_type="trade_calendar@1",
        business_date=date(2026, 7, 1),
        output_dataset="trade_calendar",
        output_version=uuid4(),
        attempt_count=1,
        max_attempts=3,
    )


def _calendar_table(exchange: str, year: int) -> pa.Table:
    row_count = 366 if isleap(year) else 365
    start = date(year, 1, 1)
    rows = []
    for offset in range(row_count):
        current = date.fromordinal(start.toordinal() + offset)
        rows.append(
            {
                "exchange": exchange,
                "cal_date": current.strftime("%Y%m%d"),
                "is_open": int(current.weekday() < 5),
                "pretrade_date": None,
            }
        )
    return pa.Table.from_pylist(rows, schema=TRADE_CAL_SPEC.schema)


def _dependency(
    store: LocalRawAssetStore,
    root: Path,
    exchange: str,
    year: int,
) -> RawDependencyAsset:
    del root
    task_id = uuid4()
    metadata = store.seal(
        RawAssetContext(
            provider="TUSHARE",
            api_name="trade_cal",
            business_date=date(year, 7, 1),
            batch_id=uuid4(),
            task_id=task_id,
        ),
        TRADE_CAL_SPEC.schema,
        (_calendar_table(exchange, year),),
    )
    return RawDependencyAsset(
        dependency_name="trade_cal",
        scope_key=f"exchange={exchange};year={year}",
        asset_id=uuid4(),
        storage_uri=metadata.storage_uri,
        content_hash=metadata.content_hash,
        schema_fingerprint=metadata.schema_fingerprint,
        row_count=metadata.row_count,
    )


def test_trade_calendar_prepares_complete_exchange_year(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    dependencies = (
        _dependency(store, tmp_path, "SSE", 2026),
        _dependency(store, tmp_path, "SZSE", 2026),
    )

    prepared = TradeCalendarProcessor().prepare(_task(), dependencies, store)

    assert prepared.rows_read == 730
    assert len(prepared.payload) == 730  # type: ignore[arg-type]
    assert prepared.rows_rejected == 0


def test_trade_calendar_rejects_incomplete_exchange_set(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    dependencies = (_dependency(store, tmp_path, "SSE", 2026),)

    with pytest.raises(ProcessingError, match="requires SSE and SZSE"):
        TradeCalendarProcessor().prepare(_task(), dependencies, store)
