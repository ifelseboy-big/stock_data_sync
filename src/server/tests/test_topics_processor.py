from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pyarrow as pa
import pytest

from app.catalog import ApiSpec
from app.catalog.tushare import (
    DC_CONCEPT_CONS_SPEC,
    DC_HOT_SPEC,
    THS_DAILY_SPEC,
    THS_HOT_SPEC,
    THS_INDEX_SPEC,
    TOP_LIST_SPEC,
)
from app.common.errors import ProcessingError
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.base import PreparedDataset
from app.modules.processing.processors.topics import (
    ConceptBoardDailyProcessor,
    ConceptBoardProcessor,
    DatedRows,
    MarketThemeMemberDailyProcessor,
    StockHotRankDailyProcessor,
    StockTopListDailyProcessor,
    ThemeIndexDailyProcessor,
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


def test_ths_daily_processor_quarantines_single_missing_close(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependency = _asset(
        store,
        batch_id,
        THS_DAILY_SPEC,
        [
            {
                "ts_code": "885921.TI",
                "trade_date": "20260713",
                "close": 1000.5,
            },
            {
                "ts_code": "861153.TI",
                "trade_date": "20260713",
                "close": None,
            },
        ],
    )

    prepared = ConceptBoardDailyProcessor().prepare(
        _task(batch_id),
        (dependency,),
        store,
    )

    payload = cast(DatedRows, prepared.payload)
    assert [row["ts_code"] for row in payload.rows] == ["885921.TI"]
    assert prepared.rows_rejected == 1
    assert "861153.TI" in prepared.warning_messages[0]


def test_ths_daily_processor_blocks_when_every_close_is_missing(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependency = _asset(
        store,
        batch_id,
        THS_DAILY_SPEC,
        [
            {
                "ts_code": "861153.TI",
                "trade_date": "20260713",
                "close": None,
            }
        ],
    )

    with pytest.raises(ProcessingError, match="close completeness threshold exceeded"):
        ConceptBoardDailyProcessor().prepare(_task(batch_id), (dependency,), store)


def test_theme_index_daily_allows_empty_target_scope_after_filter() -> None:
    session = MagicMock()
    session.scalars.return_value = []
    processor = ThemeIndexDailyProcessor()
    publisher = MagicMock()
    publisher.publish.return_value = 0
    processor._publisher = publisher
    prepared = PreparedDataset(
        DatedRows(
            BUSINESS_DATE,
            (
                {
                    "source": "THS",
                    "ts_code": "881001.TI",
                    "trade_date": BUSINESS_DATE,
                    "close": 1000,
                },
            ),
        ),
        1,
    )

    result = processor.write(session, prepared, published_at=datetime.now(UTC))

    assert result.rows_written == 0
    assert result.rows_rejected == 1
    assert "同花顺主题指数主表" in result.warning_messages[0]
    assert publisher.publish.call_args.kwargs["rows"] == ()


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
    assert (
        "l_sell, l_buy, l_amount, net_amount, net_rate, amount_rate"
        in (prepared.warning_messages[0])
    )


def test_top_list_processor_merges_name_only_duplicate_and_warns(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    first = _top_list_row() | {"name": "N示例转"}
    second = _top_list_row() | {"name": "示例转债"}
    dependency = _asset(store, batch_id, TOP_LIST_SPEC, [first, second])

    prepared = StockTopListDailyProcessor().prepare(_task(batch_id), (dependency,), store)

    payload = cast(DatedRows, prepared.payload)
    assert len(payload.rows) == 1
    assert payload.rows[0]["name"] == "N示例转"
    assert prepared.rows_rejected == 1
    assert "名称或数值精度差异" in prepared.warning_messages[0]
    assert "name" in prepared.warning_messages[0]


def test_top_list_processor_merges_small_provider_rounding_difference(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    first = _top_list_row() | {"l_buy": 323383200.0, "net_amount": 123440200.0}
    second = _top_list_row() | {"l_buy": 323383102.12, "net_amount": 123440096.73}
    dependency = _asset(store, batch_id, TOP_LIST_SPEC, [first, second])

    prepared = StockTopListDailyProcessor().prepare(_task(batch_id), (dependency,), store)

    payload = cast(DatedRows, prepared.payload)
    assert len(payload.rows) == 1
    assert str(payload.rows[0]["l_buy"]) == "323383102.12"
    assert str(payload.rows[0]["net_amount"]) == "123440096.73"
    assert prepared.rows_rejected == 1
    assert "l_buy, net_amount" in prepared.warning_messages[0]


def test_top_list_processor_quarantines_true_duplicate_conflict(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    first = _top_list_row() | {"l_buy": 100.0}
    second = _top_list_row() | {"l_buy": 200.0}
    valid = _top_list_row() | {"ts_code": "920212.BJ", "name": "有效股票"}
    dependency = _asset(store, batch_id, TOP_LIST_SPEC, [first, second, valid])

    prepared = StockTopListDailyProcessor().prepare(_task(batch_id), (dependency,), store)

    payload = cast(DatedRows, prepared.payload)
    assert [row["ts_code"] for row in payload.rows] == ["920212.BJ"]
    assert prepared.rows_read == 3
    assert prepared.rows_rejected == 2
    assert "已隔离该主键并继续发布其余数据" in prepared.warning_messages[0]
    assert "l_buy" in prepared.warning_messages[0]


def test_theme_member_processor_deduplicates_identical_paged_rows(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    row = _theme_member_row()
    dependency = _asset(store, batch_id, DC_CONCEPT_CONS_SPEC, [row, row])

    prepared = MarketThemeMemberDailyProcessor().prepare(
        _task(batch_id),
        (dependency,),
        store,
    )

    payload = cast(DatedRows, prepared.payload)
    assert len(payload.rows) == 1
    assert prepared.rows_read == 2
    assert prepared.rows_rejected == 1
    assert prepared.warning_messages == (
        "dc_concept_cons 返回 1 条完全重复记录，加工时已确定性去重",
    )


def test_theme_member_processor_rejects_conflicting_paged_rows(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    first = _theme_member_row()
    second = first | {"reason": "不同的入选原因"}
    dependency = _asset(store, batch_id, DC_CONCEPT_CONS_SPEC, [first, second])

    with pytest.raises(ProcessingError, match="conflicting duplicate key"):
        MarketThemeMemberDailyProcessor().prepare(
            _task(batch_id),
            (dependency,),
            store,
        )


def test_dc_hot_processor_selects_latest_complete_snapshot(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependency = _asset(
        store,
        batch_id,
        DC_HOT_SPEC,
        [
            _dc_hot_row("000001.SZ", 1, "2026-07-13 21:30:00"),
            _dc_hot_row("000002.SZ", 2, "2026-07-13 21:30:00"),
            _dc_hot_row("000002.SZ", 1, "2026-07-13 22:30:00"),
            _dc_hot_row("000001.SZ", 2, "2026-07-13 22:30:00"),
            _dc_hot_row("000003.SZ", 1, "2026-07-13 22:31:00"),
        ],
        scope_key="trade_date=20260713;hot_type=人气榜;market=A股市场;is_new=Y",
    )

    prepared = StockHotRankDailyProcessor().prepare(_task(batch_id), (dependency,), store)

    payload = cast(DatedRows, prepared.payload)
    assert [(row["ts_code"], row["rank"]) for row in payload.rows] == [
        ("000002.SZ", 1),
        ("000001.SZ", 2),
    ]
    assert prepared.rows_read == 5
    assert prepared.rows_rejected == 3


def test_dc_hot_processor_rejects_duplicate_rank_in_latest_snapshot(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependency = _asset(
        store,
        batch_id,
        DC_HOT_SPEC,
        [
            _dc_hot_row("000001.SZ", 1, "2026-07-13 22:30:00"),
            _dc_hot_row("000002.SZ", 1, "2026-07-13 22:30:00"),
        ],
        scope_key="trade_date=20260713;hot_type=人气榜;market=A股市场;is_new=Y",
    )

    with pytest.raises(ProcessingError, match="duplicate rank"):
        StockHotRankDailyProcessor().prepare(_task(batch_id), (dependency,), store)


def test_ths_hot_processor_selects_latest_complete_minute_snapshot(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependency = _asset(
        store,
        batch_id,
        THS_HOT_SPEC,
        [
            _ths_hot_row("000001.SZ", 1, "2026-07-13 21:30:00"),
            _ths_hot_row("000002.SZ", 2, "2026-07-13 21:30:01"),
            _ths_hot_row("000002.SZ", 1, "2026-07-13 22:00:00"),
            _ths_hot_row("000001.SZ", 2, "2026-07-13 22:00:01"),
            _ths_hot_row("000003.SZ", 1, "2026-07-13 22:01:00"),
        ],
        scope_key="trade_date=20260713;market=热股;is_new=N",
    )

    prepared = StockHotRankDailyProcessor().prepare(_task(batch_id), (dependency,), store)

    payload = cast(DatedRows, prepared.payload)
    assert [(row["ts_code"], row["rank"]) for row in payload.rows] == [
        ("000002.SZ", 1),
        ("000001.SZ", 2),
    ]
    assert prepared.rows_read == 5
    assert prepared.rows_rejected == 3


def test_ths_hot_processor_splits_two_snapshots_from_the_same_minute(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependency = _asset(
        store,
        batch_id,
        THS_HOT_SPEC,
        [
            _ths_hot_row("000001.SZ", 1, "2026-07-13 14:00:01"),
            _ths_hot_row("000002.SZ", 2, "2026-07-13 14:00:02"),
            _ths_hot_row("000002.SZ", 1, "2026-07-13 14:00:08"),
            _ths_hot_row("000001.SZ", 2, "2026-07-13 14:00:09"),
        ],
        scope_key="trade_date=20260713;market=热股;is_new=N",
    )

    prepared = StockHotRankDailyProcessor().prepare(_task(batch_id), (dependency,), store)

    payload = cast(DatedRows, prepared.payload)
    assert [(row["ts_code"], row["rank"]) for row in payload.rows] == [
        ("000002.SZ", 1),
        ("000001.SZ", 2),
    ]
    assert prepared.rows_read == 4
    assert prepared.rows_rejected == 2


def test_ths_hot_processor_keeps_complete_snapshot_before_partial_tail(
    tmp_path: Path,
) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependency = _asset(
        store,
        batch_id,
        THS_HOT_SPEC,
        [
            _ths_hot_row("000001.SZ", 1, "2026-07-13 14:00:01"),
            _ths_hot_row("000002.SZ", 2, "2026-07-13 14:00:02"),
            _ths_hot_row("000003.SZ", 1, "2026-07-13 14:00:08"),
        ],
        scope_key="trade_date=20260713;market=热股;is_new=N",
    )

    prepared = StockHotRankDailyProcessor().prepare(_task(batch_id), (dependency,), store)

    payload = cast(DatedRows, prepared.payload)
    assert [(row["ts_code"], row["rank"]) for row in payload.rows] == [
        ("000001.SZ", 1),
        ("000002.SZ", 2),
    ]
    assert prepared.rows_rejected == 1


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


def _theme_member_row() -> dict[str, object]:
    return {
        "ts_code": "000066.SZ",
        "trade_date": "20260713",
        "name": "中国长城",
        "theme_code": "000677.DC",
        "industry_code": "BK0735",
        "industry": "计算机设备",
        "reason": "车联网云",
        "hot_num": 228,
    }


def _dc_hot_row(ts_code: str, rank: int, rank_time: str) -> dict[str, object]:
    return {
        "trade_date": "20260713",
        "data_type": "A股市场",
        "ts_code": ts_code,
        "ts_name": f"股票{rank}",
        "rank": rank,
        "pct_change": 1.2,
        "current_price": 10.5,
        "hot": 1000.0,
        "concept": '["示例"]',
        "rank_time": rank_time,
    }


def _ths_hot_row(ts_code: str, rank: int, rank_time: str) -> dict[str, object]:
    return {
        "trade_date": "20260713",
        "data_type": "热股",
        "ts_code": ts_code,
        "ts_name": f"股票{rank}",
        "rank": rank,
        "pct_change": 1.2,
        "current_price": 10.5,
        "concept": '["示例"]',
        "rank_reason": "热榜",
        "hot": 1000.0,
        "rank_time": rank_time,
    }


def _asset(
    store: LocalRawAssetStore,
    batch_id: UUID,
    spec: ApiSpec,
    rows: list[dict[str, object]],
    *,
    scope_key: str = "test",
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
        scope_key=scope_key,
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
