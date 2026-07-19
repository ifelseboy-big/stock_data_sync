from datetime import date

import pytest

from app.modules.partitions.service import PARTITIONED_TABLES, month_start, planned_partitions


def test_month_start_handles_year_boundary() -> None:
    assert month_start(date(2026, 12, 20)) == date(2026, 12, 1)
    assert month_start(date(2026, 12, 20), 1) == date(2027, 1, 1)


def test_partition_plan_includes_current_and_three_future_months() -> None:
    specs = planned_partitions(date(2026, 7, 19), months_ahead=3)

    assert len(specs) == len(PARTITIONED_TABLES) * 4
    assert specs[0].partition_table == "stock_daily_p202607"
    assert specs[0].start_date == date(2026, 7, 1)
    assert specs[0].end_date == date(2026, 8, 1)
    assert specs[-1].partition_table == "market_theme_member_daily_p202610"


def test_partition_sql_uses_fixed_generated_identifiers() -> None:
    spec = planned_partitions(date(2026, 7, 19), months_ahead=0)[0]

    assert spec.create_sql() == (
        "CREATE TABLE IF NOT EXISTS stock_daily_p202607 PARTITION OF stock_daily "
        "FOR VALUES FROM ('2026-07-01') TO ('2026-08-01')"
    )


def test_partition_plan_rejects_negative_horizon() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        planned_partitions(date(2026, 7, 19), months_ahead=-1)
