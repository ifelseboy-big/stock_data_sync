from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pyarrow as pa
import pytest

from app.catalog import ApiSpec
from app.catalog.bse_codes import (
    BSE_CODE_ALIASES,
    CORPORATE_ACTION_CODE_ALIASES,
    canonical_stock_code,
)
from app.catalog.tushare import (
    ADJ_FACTOR_SPEC,
    DAILY_BASIC_SPEC,
    DAILY_SPEC,
    MONEYFLOW_SPEC,
    STK_FACTOR_SPEC,
    SUSPEND_SPEC,
)
from app.common.errors import ProcessingError
from app.modules.processing.domain import ClaimedProcessingTask, RawDependencyAsset
from app.modules.processing.processors.base import PreparedDataset
from app.modules.processing.processors.stock_daily import (
    StockDailyCoreProcessor,
    StockDailyLimitProcessor,
    StockMoneyflowDailyProcessor,
    StockSuspendDailyProcessor,
    StockTechnicalDailyProcessor,
    _existing_stock_daily_patch_values,
    _validate_limit_pre_close,
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


def test_stock_daily_core_quarantines_conflicting_daily_basic_enrichment(
    tmp_path: Path,
) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    daily = _daily_source("603081.SH")
    daily.update(
        {
            "open": 11.51,
            "high": 11.77,
            "low": 11.28,
            "close": 11.6,
            "pre_close": 11.51,
            "change": 0.09,
            "pct_chg": 0.7819,
        }
    )
    dependencies = (
        _asset(store, batch_id, DAILY_SPEC, [_daily_source("000001.SZ"), daily]),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [
                {
                    "ts_code": "603081.SH",
                    "trade_date": "20260717",
                    "close": 11.51,
                    "pe": 16.4368,
                    "total_mv": 471481.3377,
                },
                {"ts_code": "000001.SZ", "trade_date": "20260717", "close": 10.5},
            ],
        ),
        _asset(
            store,
            batch_id,
            ADJ_FACTOR_SPEC,
            [
                {"ts_code": "603081.SH", "trade_date": "20260717", "adj_factor": 1.25},
                {"ts_code": "000001.SZ", "trade_date": "20260717", "adj_factor": 1.25},
            ],
        ),
    )

    prepared = StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    conflicting_row = next(row for row in rows if row["ts_code"] == "603081.SH")
    assert conflicting_row["close"] == Decimal("11.6")
    assert conflicting_row["adj_factor"] == Decimal("1.25")
    assert conflicting_row["pe"] is None
    assert conflicting_row["total_mv"] is None
    assert prepared.rows_rejected == 1
    assert "daily_basic 已隔离 1 条" in prepared.warning_messages[0]
    assert "603081.SH" in prepared.warning_messages[0]


def test_stock_daily_core_allows_small_daily_basic_key_gap(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    codes = ("000001.SZ", "920123.BJ")
    dependencies = (
        _asset(store, batch_id, DAILY_SPEC, [_daily_source(code) for code in codes]),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [{"ts_code": "000001.SZ", "trade_date": "20260717", "close": 10.5}],
        ),
        _asset(
            store,
            batch_id,
            ADJ_FACTOR_SPEC,
            [{"ts_code": code, "trade_date": "20260717", "adj_factor": 1.25} for code in codes],
        ),
    )

    prepared = StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    bse_row = next(row for row in rows if row["ts_code"] == "920123.BJ")
    assert bse_row["close"] == Decimal("10.5")
    assert bse_row["turnover_rate"] is None
    assert prepared.rows_rejected == 1
    assert "daily_basic row is missing" in prepared.warning_messages[0]


def test_stock_daily_core_blocks_large_daily_basic_gap(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    codes = tuple(f"{index:06d}.SZ" for index in range(101))
    dependencies = (
        _asset(store, batch_id, DAILY_SPEC, [_daily_source(code) for code in codes]),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [{"ts_code": code, "trade_date": "20260717", "close": 10.5} for code in codes[:-2]],
        ),
        _asset(
            store,
            batch_id,
            ADJ_FACTOR_SPEC,
            [{"ts_code": code, "trade_date": "20260717", "adj_factor": 1.25} for code in codes],
        ),
    )

    with pytest.raises(ProcessingError, match="quality threshold exceeded"):
        StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)


def test_stock_daily_core_isolates_systematic_bse_previous_close_lag(
    tmp_path: Path,
) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    changed_bse_codes = tuple(f"920{index:03d}.BJ" for index in range(108))
    unchanged_bse_codes = tuple(f"921{index:03d}.BJ" for index in range(12))
    missing_bse_codes = tuple(f"922{index:03d}.BJ" for index in range(8))
    other_codes = tuple(f"00{index:04d}.SZ" for index in range(872))
    bse_codes = (*changed_bse_codes, *unchanged_bse_codes, *missing_bse_codes)
    codes = (*bse_codes, *other_codes)
    daily_rows = [_daily_source(code) for code in (*changed_bse_codes, *missing_bse_codes)]
    daily_rows.extend(_unchanged_daily_source(code) for code in unchanged_bse_codes)
    daily_rows.extend(_daily_source(code) for code in other_codes)
    dependencies = (
        _asset(store, batch_id, DAILY_SPEC, daily_rows),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [
                {
                    "ts_code": code,
                    "trade_date": "20260717",
                    "close": 10.0 if code.endswith(".BJ") else 10.5,
                    "pe": 12.3,
                }
                for code in (*changed_bse_codes, *unchanged_bse_codes, *other_codes)
            ],
        ),
        _asset(
            store,
            batch_id,
            ADJ_FACTOR_SPEC,
            [{"ts_code": code, "trade_date": "20260717", "adj_factor": 1.25} for code in codes],
        ),
    )

    prepared = StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert len(rows) == len(codes)
    assert prepared.rows_rejected == len(bse_codes)
    assert all(row["pe"] is None for row in rows if row["ts_code"].endswith(".BJ"))
    assert "daily_basic 已隔离 128 条" in prepared.warning_messages[0]
    assert "BSE segment matches previous-close snapshot" in prepared.warning_messages[0]


def test_stock_daily_core_isolates_bse_daily_basic_close_mismatches(
    tmp_path: Path,
) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    codes = tuple(f"92{index:04d}.BJ" for index in range(101))
    dependencies = (
        _asset(store, batch_id, DAILY_SPEC, [_daily_source(code) for code in codes]),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [
                {
                    "ts_code": code,
                    "trade_date": "20260717",
                    "close": 9.9 if index < 2 else 10.5,
                }
                for index, code in enumerate(codes)
            ],
        ),
        _asset(
            store,
            batch_id,
            ADJ_FACTOR_SPEC,
            [{"ts_code": code, "trade_date": "20260717", "adj_factor": 1.25} for code in codes],
        ),
    )

    prepared = StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert len(rows) == len(codes)
    assert prepared.rows_rejected == 2
    assert "daily_basic 已隔离 2 条" in prepared.warning_messages[0]
    assert "daily/daily_basic close mismatch" in prepared.warning_messages[0]


def test_stock_daily_core_allows_historical_bse_daily_basic_coverage_gap(
    tmp_path: Path,
) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    bse_codes = tuple(f"920{index:03d}.BJ" for index in range(136))
    other_codes = tuple(f"{index:06d}.SZ" for index in range(4_526))
    codes = (*bse_codes, *other_codes)
    dependencies = (
        _asset(store, batch_id, DAILY_SPEC, [_daily_source(code) for code in codes]),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [
                {"ts_code": code, "trade_date": "20260717", "close": 10.5}
                for code in other_codes
            ]
            + [
                {
                    "ts_code": bse_codes[-1],
                    "trade_date": "20260717",
                    "close": 10.5,
                    "turnover_rate": 1.5,
                }
            ],
        ),
        _asset(
            store,
            batch_id,
            ADJ_FACTOR_SPEC,
            [
                {"ts_code": code, "trade_date": "20260717", "adj_factor": 1.25}
                for code in codes
            ],
        ),
    )

    prepared = StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert len(rows) == 4_662
    assert prepared.rows_rejected == 135
    bse_rows = [row for row in rows if cast(str, row["ts_code"]).endswith(".BJ")]
    assert sum(row["turnover_rate"] is None for row in bse_rows) == 135
    assert sum(row["turnover_rate"] == Decimal("1.5") for row in bse_rows) == 1
    assert "daily_basic 已隔离 135 条" in prepared.warning_messages[0]
    assert "daily_basic row is missing" in prepared.warning_messages[0]


def test_stock_daily_core_isolates_single_missing_previous_close(
    tmp_path: Path,
) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    codes = tuple(f"00{index:04d}.SZ" for index in range(100))
    incomplete = _daily_source("920570.BJ")
    incomplete.update({"pre_close": None, "change": None, "pct_chg": None})
    daily_rows = [_daily_source(code) for code in codes]
    daily_rows.append(incomplete)
    dependencies = (
        _asset(store, batch_id, DAILY_SPEC, daily_rows),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [{"ts_code": code, "trade_date": "20260717", "close": 10.5} for code in codes],
        ),
        _asset(
            store,
            batch_id,
            ADJ_FACTOR_SPEC,
            [
                {"ts_code": code, "trade_date": "20260717", "adj_factor": 1.25}
                for code in (*codes, "920570.BJ")
            ],
        ),
    )

    prepared = StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert len(rows) == len(codes)
    assert all(row["ts_code"] != "920570.BJ" for row in rows)
    assert prepared.rows_rejected == 1
    assert "daily_basic row is missing" in prepared.warning_messages[0]
    assert "daily 已隔离 1 条" in prepared.warning_messages[1]
    assert "daily pre_close is missing for 920570.BJ" in prepared.warning_messages[1]


def test_stock_daily_core_derives_change_from_close_and_previous_close(
    tmp_path: Path,
) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    daily = {
        "ts_code": "603005.SH",
        "trade_date": "20260717",
        "open": 93.0,
        "high": 94.88,
        "low": 85.91,
        "close": 86.8,
        "pre_close": 91.89,
        "change": -5.03,
        "pct_chg": -5.4739,
        "vol": 180173.44,
        "amount": 1637884.572,
    }
    dependencies = (
        _asset(store, batch_id, DAILY_SPEC, [daily]),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [{"ts_code": "603005.SH", "trade_date": "20260717", "close": 86.8}],
        ),
        _asset(
            store,
            batch_id,
            ADJ_FACTOR_SPEC,
            [{"ts_code": "603005.SH", "trade_date": "20260717", "adj_factor": 1.25}],
        ),
    )

    prepared = StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert rows[0]["change"] == Decimal("-5.09")
    assert cast(Decimal, rows[0]["pct_chg"]).quantize(Decimal("0.000001")) == Decimal(
        "-5.539232"
    )
    assert prepared.rows_rejected == 0
    assert "根据 close/pre_close 重算 1 条" in prepared.warning_messages[0]
    assert "603005.SH" in prepared.warning_messages[0]


def test_stock_daily_core_rejects_zero_previous_close_before_deriving_pct_change(
    tmp_path: Path,
) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    daily = _daily_source("000001.SZ")
    daily.update({"pre_close": 0, "change": None, "pct_chg": None})
    dependencies = (
        _asset(store, batch_id, DAILY_SPEC, [daily]),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [{"ts_code": "000001.SZ", "trade_date": "20260717", "close": 10.5}],
        ),
        _asset(
            store,
            batch_id,
            ADJ_FACTOR_SPEC,
            [{"ts_code": "000001.SZ", "trade_date": "20260717", "adj_factor": 1.25}],
        ),
    )

    with pytest.raises(ProcessingError, match="non-positive price"):
        StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)


def test_stock_daily_core_blocks_large_missing_previous_close_gap(
    tmp_path: Path,
) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    codes = tuple(f"00{index:04d}.SZ" for index in range(99))
    incomplete_codes = ("920570.BJ", "920571.BJ")
    daily_rows = [_daily_source(code) for code in codes]
    for code in incomplete_codes:
        row = _daily_source(code)
        row.update({"pre_close": None, "change": None, "pct_chg": None})
        daily_rows.append(row)
    dependencies = (
        _asset(store, batch_id, DAILY_SPEC, daily_rows),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [
                {"ts_code": code, "trade_date": "20260717", "close": 10.5}
                for code in (*codes, *incomplete_codes)
            ],
        ),
        _asset(
            store,
            batch_id,
            ADJ_FACTOR_SPEC,
            [
                {"ts_code": code, "trade_date": "20260717", "adj_factor": 1.25}
                for code in (*codes, *incomplete_codes)
            ],
        ),
    )

    with pytest.raises(ProcessingError, match="daily price quality threshold exceeded"):
        StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)


def test_stock_daily_core_still_requires_adj_factor_coverage(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependencies = (
        _asset(store, batch_id, DAILY_SPEC, [_daily_source("000001.SZ")]),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [{"ts_code": "000001.SZ", "trade_date": "20260717", "close": 10.5}],
        ),
        _asset(store, batch_id, ADJ_FACTOR_SPEC, []),
    )

    with pytest.raises(ProcessingError, match="adj_factor does not cover daily"):
        StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)


def test_bse_code_mapping_uses_complete_official_table() -> None:
    assert len(BSE_CODE_ALIASES) == 248
    assert len(set(BSE_CODE_ALIASES.values())) == 248
    assert canonical_stock_code("831396.BJ") == "920496.BJ"
    assert canonical_stock_code("836961.BJ") == "920061.BJ"
    assert canonical_stock_code("000001.SZ") == "000001.SZ"


def test_corporate_action_code_mapping_uses_current_security_code() -> None:
    assert CORPORATE_ACTION_CODE_ALIASES == {"300114.SZ": "302132.SZ"}
    assert canonical_stock_code("300114.SZ") == "302132.SZ"


def test_stock_daily_core_prefers_current_code_when_alias_rows_overlap(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependencies = (
        _asset(
            store,
            batch_id,
            DAILY_SPEC,
            [_daily_source("300114.SZ"), _daily_source("302132.SZ")],
        ),
        _asset(
            store,
            batch_id,
            DAILY_BASIC_SPEC,
            [
                {
                    "ts_code": "300114.SZ",
                    "trade_date": "20260717",
                    "close": 10.5,
                },
                {
                    "ts_code": "302132.SZ",
                    "trade_date": "20260717",
                    "close": 10.5,
                },
            ],
        ),
        _asset(
            store,
            batch_id,
            ADJ_FACTOR_SPEC,
            [
                {
                    "ts_code": "300114.SZ",
                    "trade_date": "20260717",
                    "adj_factor": 1.25,
                },
                {
                    "ts_code": "302132.SZ",
                    "trade_date": "20260717",
                    "adj_factor": 1.2501,
                },
            ],
        ),
    )

    prepared = StockDailyCoreProcessor().prepare(_task(batch_id), dependencies, store)

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert rows[0]["ts_code"] == "302132.SZ"
    assert rows[0]["adj_factor"] == Decimal("1.2501")
    assert prepared.rows_rejected == 3
    assert len(prepared.warning_messages) == 3
    assert all("300114.SZ->302132.SZ" in warning for warning in prepared.warning_messages)


def test_stock_moneyflow_maps_historical_corporate_action_code(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    batch_id = uuid4()
    dependency = _asset(
        store,
        batch_id,
        MONEYFLOW_SPEC,
        [{"ts_code": "300114.SZ", "trade_date": "20260717"}],
    )

    prepared = StockMoneyflowDailyProcessor().prepare(
        _task(batch_id),
        (dependency,),
        store,
    )

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert rows[0]["ts_code"] == "302132.SZ"
    assert prepared.rows_rejected == 0
    assert "300114.SZ->302132.SZ" in prepared.warning_messages[0]


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

    prepared = StockTechnicalDailyProcessor().prepare(_task(batch_id), dependencies, store)

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert len(rows) == 1
    assert rows[0]["ts_code"] == "920017.BJ"
    assert rows[0]["macd"] == Decimal("2.0")
    assert prepared.rows_rejected == 1
    assert "430017.BJ->920017.BJ" in prepared.warning_messages[0]


def test_stock_technical_prefers_current_code_when_alias_values_conflict(tmp_path: Path) -> None:
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

    prepared = StockTechnicalDailyProcessor().prepare(_task(batch_id), dependencies, store)

    rows = cast(tuple[PreparedRow, ...], prepared.payload)
    assert len(rows) == 1
    assert rows[0]["ts_code"] == "920017.BJ"
    assert rows[0]["source_close"] == Decimal("10.6")
    assert rows[0]["macd"] == Decimal("2.0")
    assert prepared.rows_rejected == 1
    assert "430017.BJ->920017.BJ" in prepared.warning_messages[0]


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


def test_stock_daily_limit_uses_core_pre_close_when_provider_value_is_missing() -> None:
    _validate_limit_pre_close("000001.SZ", Decimal("10.23"), None)


def test_stock_daily_limit_still_rejects_conflicting_pre_close() -> None:
    with pytest.raises(ProcessingError, match="pre_close mismatch"):
        _validate_limit_pre_close(
            "000001.SZ",
            Decimal("10.23"),
            Decimal("10.24"),
        )


def test_stock_daily_core_write_filters_rows_outside_current_stock_master() -> None:
    row: PreparedRow = {
        "ts_code": "000001.SZ",
        "trade_date": BUSINESS_DATE,
        "open": Decimal("10.0"),
        "high": Decimal("11.0"),
        "low": Decimal("9.5"),
        "close": Decimal("10.5"),
        "pre_close": Decimal("10.0"),
        "change": Decimal("0.5"),
        "pct_chg": Decimal("5.0"),
        "limit_status": None,
        "up_limit": None,
        "down_limit": None,
    }
    session = MagicMock()
    session.scalars.return_value = ["000001.SZ"]
    _mock_current_limit_release(session)
    session.execute.return_value = [
        (
            "000001.SZ",
            Decimal("10.5"),
            Decimal("10.0"),
            1,
            Decimal("11.0"),
            Decimal("9.0"),
        ),
    ]
    processor = StockDailyCoreProcessor()
    publisher = MagicMock()
    publisher.publish.return_value = 1
    processor._publisher = publisher

    result = processor.write(
        session,
        PreparedDataset(
            (
                row,
                row | {"ts_code": "000022.SZ"},
            ),
            2,
        ),
        published_at=datetime.now(UTC),
    )

    assert result.rows_written == 1
    assert result.rows_rejected == 1
    assert "000022.SZ" in result.warning_messages[0]
    published_rows = publisher.publish.call_args.kwargs["rows"]
    assert [published_row["ts_code"] for published_row in published_rows] == ["000001.SZ"]
    assert published_rows[0]["limit_status"] == 1
    assert published_rows[0]["up_limit"] == Decimal("11.0")
    assert published_rows[0]["down_limit"] == Decimal("9.0")


def test_stock_moneyflow_write_filters_rows_outside_current_stock_master() -> None:
    session = MagicMock()
    session.scalars.return_value = ["000001.SZ"]
    processor = StockMoneyflowDailyProcessor()
    publisher = MagicMock()
    publisher.publish.return_value = 1
    processor._publisher = publisher
    rows: tuple[PreparedRow, ...] = (
        {"ts_code": "000001.SZ", "trade_date": BUSINESS_DATE},
        {"ts_code": "000043.SZ", "trade_date": BUSINESS_DATE},
    )

    result = processor.write(
        session,
        PreparedDataset(rows, 2),
        published_at=datetime.now(UTC),
    )

    assert result.rows_written == 1
    assert result.rows_rejected == 1
    assert "000043.SZ" in result.warning_messages[0]
    assert publisher.publish.call_args.kwargs["rows"][0]["ts_code"] == "000001.SZ"


def test_stock_daily_limit_allows_no_rows_after_core_scope_filter() -> None:
    session = MagicMock()
    session.execute.return_value = []
    processor = StockDailyLimitProcessor()
    publisher = MagicMock()
    publisher.publish.return_value = 0
    processor._publisher = publisher
    row: PreparedRow = {
        "ts_code": "000022.SZ",
        "trade_date": BUSINESS_DATE,
        "source_pre_close": Decimal("10"),
        "up_limit": Decimal("11"),
        "down_limit": Decimal("9"),
    }

    result = processor.write(
        session,
        PreparedDataset((row,), 1),
        published_at=datetime.now(UTC),
    )

    assert result.rows_written == 0
    assert result.rows_rejected == 1
    assert "stock_daily.core" in result.warning_messages[0]
    assert publisher.publish.call_args.kwargs["rows"] == ()


def test_existing_stock_daily_patch_values_are_keyed_by_security() -> None:
    session = MagicMock()
    _mock_current_limit_release(session)
    session.execute.return_value = [
        (
            "000001.SZ",
            Decimal("10.5"),
            Decimal("10.0"),
            1,
            Decimal("11.0"),
            Decimal("9.0"),
        ),
    ]

    values = _existing_stock_daily_patch_values(
        session,
        (
            {
                "ts_code": "000001.SZ",
                "trade_date": BUSINESS_DATE,
                "close": Decimal("10.5"),
                "pre_close": Decimal("10.0"),
            },
        ),
        BUSINESS_DATE,
    )

    assert values == {
        "000001.SZ": {
            "limit_status": 1,
            "up_limit": Decimal("11.0"),
            "down_limit": Decimal("9.0"),
        }
    }


def test_existing_stock_daily_patch_values_drop_stale_limit_columns() -> None:
    session = MagicMock()
    _mock_current_limit_release(session)
    session.execute.return_value = [
        (
            "000001.SZ",
            Decimal("10.4"),
            Decimal("10.0"),
            1,
            Decimal("11.0"),
            Decimal("9.0"),
        ),
    ]

    values = _existing_stock_daily_patch_values(
        session,
        (
            {
                "ts_code": "000001.SZ",
                "trade_date": BUSINESS_DATE,
                "close": Decimal("10.5"),
                "pre_close": Decimal("10.0"),
            },
        ),
        BUSINESS_DATE,
    )

    assert values == {}


def test_existing_stock_daily_patch_values_require_valid_limit_release() -> None:
    session = MagicMock()
    session.get.return_value = None

    values = _existing_stock_daily_patch_values(
        session,
        (
            {
                "ts_code": "000001.SZ",
                "trade_date": BUSINESS_DATE,
                "close": Decimal("10.5"),
                "pre_close": Decimal("10.0"),
            },
        ),
        BUSINESS_DATE,
    )

    assert values == {}
    session.execute.assert_not_called()


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


def _mock_current_limit_release(session: MagicMock) -> None:
    core_process_id = uuid4()
    limit_process_id = uuid4()
    session.get.side_effect = (
        SimpleNamespace(process_id=core_process_id),
        SimpleNamespace(process_id=limit_process_id),
        SimpleNamespace(
            status="READY",
            resolved_release_process_id=core_process_id,
        ),
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


def _daily_source(ts_code: str) -> dict[str, object]:
    return {
        "ts_code": ts_code,
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


def _unchanged_daily_source(ts_code: str) -> dict[str, object]:
    row = _daily_source(ts_code)
    row.update({"close": 10.0, "change": 0.0, "pct_chg": 0.0})
    return row


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
