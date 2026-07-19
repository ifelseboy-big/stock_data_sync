from datetime import date
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pyarrow as pa
import pytest

from app.catalog import ApiSpec
from app.catalog.tushare import THS_INDEX_SPEC, TOP_LIST_SPEC
from app.common.errors import ProcessingError
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.topics import (
    ConceptBoardProcessor,
    DatedRows,
    StockTopListDailyProcessor,
    ThemeIndexProcessor,
)
from app.storage import LocalRawAssetStore, RawAssetContext

BUSINESS_DATE = date(2026, 7, 13)


def test_ths_index_rows_are_split_between_concepts_and_themes(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependency = _asset(
        store,
        batch_id,
        THS_INDEX_SPEC,
        [
            {
                "ts_code": "885921.TI",
                "name": "储能",
                "count": 300,
                "exchange": "A",
                "list_date": "20200101",
                "type": "N",
            },
            {
                "ts_code": "700056.TI",
                "name": "宁组合",
                "count": None,
                "exchange": "A",
                "list_date": None,
                "type": "TH",
            },
        ],
    )

    concept = ConceptBoardProcessor().prepare(_task(batch_id), (dependency,), store)
    theme = ThemeIndexProcessor().prepare(_task(batch_id), (dependency,), store)

    concept_rows = cast(tuple[dict[str, object], ...], concept.payload)
    theme_rows = cast(tuple[dict[str, object], ...], theme.payload)
    assert [row["ts_code"] for row in concept_rows] == ["885921.TI"]
    assert [row["ts_code"] for row in theme_rows] == ["700056.TI"]
    assert concept.rows_rejected == 1
    assert theme.rows_rejected == 1


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


def test_top_list_processor_keeps_more_complete_duplicate_and_warns(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    sparse = _top_list_row()
    complete = sparse | {
        "l_sell": 80742933.2,
        "l_buy": 120952075.83,
        "l_amount": 201695009.03,
        "net_amount": 40209142.63,
        "net_rate": 4.84,
        "amount_rate": 24.27,
    }
    dependency = _asset(store, batch_id, TOP_LIST_SPEC, [sparse, complete])

    prepared = StockTopListDailyProcessor().prepare(
        _task(batch_id),
        (dependency,),
        store,
    )

    payload = cast(DatedRows, prepared.payload)
    assert len(payload.rows) == 1
    assert str(payload.rows[0]["l_buy"]) == "120952075.83"
    assert prepared.rows_rejected == 1
    assert len(prepared.warning_messages) == 1
    assert "920211.BJ" in prepared.warning_messages[0]
    assert "l_sell, l_buy, l_amount, net_amount, net_rate, amount_rate" in (
        prepared.warning_messages[0]
    )


def test_top_list_processor_still_rejects_true_duplicate_conflicts(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    first = _top_list_row() | {"l_buy": 100.0}
    second = _top_list_row() | {"l_buy": 200.0}
    dependency = _asset(store, batch_id, TOP_LIST_SPEC, [first, second])

    with pytest.raises(ProcessingError, match="conflicting duplicate key"):
        StockTopListDailyProcessor().prepare(
            _task(batch_id),
            (dependency,),
            store,
        )


def _top_list_row() -> dict[str, object]:
    return {
        "trade_date": "20260713",
        "ts_code": "920211.BJ",
        "name": "新睿电子",
        "close": 180.5,
        "pct_change": 14.0384,
        "turnover_rate": 53.54,
        "amount": 831086200.0,
        "l_sell": None,
        "l_buy": None,
        "l_amount": None,
        "net_amount": None,
        "net_rate": None,
        "amount_rate": None,
        "float_values": 1039680000.0,
        "reason": "北交所股票连续3个交易日内日收盘价涨跌幅偏离值累计达到+40%(-40%)",
    }


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
