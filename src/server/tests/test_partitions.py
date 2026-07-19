from datetime import date

import pytest

from app.modules.partitions.service import (
    PARTITIONED_TABLES,
    month_start,
    planned_partitions,
    planned_partitions_for_range,
    planned_partitions_for_table,
)


def test_month_start_handles_year_boundary() -> None:
    assert month_start(date(2026, 12, 20)) == date(2026, 12, 1)
    assert month_start(date(2026, 12, 20), 1) == date(2027, 1, 1)


def test_partition_plan_includes_current_and_three_future_months() -> None:
    specs = planned_partitions(date(2026, 7, 19), months_ahead=3)

    assert len(specs) == len(PARTITIONED_TABLES) * 4
    assert specs[0].partition_table == "stock_daily_p202607"
    assert specs[0].start_date == date(2026, 7, 1)
    assert specs[0].end_date == date(2026, 8, 1)
    assert specs[-1].partition_table == "etf_share_size_daily_p202610"


def test_partition_sql_uses_fixed_generated_identifiers() -> None:
    spec = planned_partitions(date(2026, 7, 19), months_ahead=0)[0]

    assert spec.create_sql() == (
        "CREATE TABLE IF NOT EXISTS stock_daily_p202607 PARTITION OF stock_daily "
        "FOR VALUES FROM ('2026-07-01') TO ('2026-08-01')"
    )


def test_partition_plan_rejects_negative_horizon() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        planned_partitions(date(2026, 7, 19), months_ahead=-1)


def test_partition_plan_for_backfill_range_includes_every_touched_month() -> None:
    specs = planned_partitions_for_range(date(2025, 12, 31), date(2026, 2, 1))

    stock_partitions = [
        item.partition_table for item in specs if item.parent_table == "stock_daily"
    ]
    assert stock_partitions == [
        "stock_daily_p202512",
        "stock_daily_p202601",
        "stock_daily_p202602",
    ]


def test_partition_plan_for_backfill_range_rejects_reversed_dates() -> None:
    with pytest.raises(ValueError, match="end_date"):
        planned_partitions_for_range(date(2026, 2, 1), date(2026, 1, 31))


def test_partition_plan_for_write_targets_only_touched_table_and_months() -> None:
    specs = planned_partitions_for_table(
        "etf_daily",
        (date(2026, 8, 1), date(2026, 7, 31), date(2026, 7, 1)),
    )

    assert [item.partition_table for item in specs] == [
        "etf_daily_p202607",
        "etf_daily_p202608",
    ]


def test_partition_plan_for_write_rejects_unmanaged_table() -> None:
    with pytest.raises(ValueError, match="not a managed partitioned table"):
        planned_partitions_for_table("theme_index_daily", (date(2026, 7, 19),))
