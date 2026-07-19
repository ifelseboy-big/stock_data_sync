from dataclasses import dataclass
from datetime import date

from sqlalchemy.engine import Connection

PARTITIONED_TABLES = (
    "stock_daily",
    "stock_technical_daily",
    "stock_moneyflow_daily",
    "market_theme_member_daily",
    "etf_daily",
    "etf_share_size_daily",
)


@dataclass(frozen=True, slots=True)
class PartitionSpec:
    parent_table: str
    partition_table: str
    start_date: date
    end_date: date

    def create_sql(self) -> str:
        return (
            f"CREATE TABLE IF NOT EXISTS {self.partition_table} "
            f"PARTITION OF {self.parent_table} "
            f"FOR VALUES FROM ('{self.start_date.isoformat()}') "
            f"TO ('{self.end_date.isoformat()}')"
        )


def month_start(value: date, offset: int = 0) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def planned_partitions(reference_date: date, months_ahead: int) -> tuple[PartitionSpec, ...]:
    if months_ahead < 0:
        raise ValueError("months_ahead must be non-negative")

    specs: list[PartitionSpec] = []
    for parent_table in PARTITIONED_TABLES:
        for offset in range(months_ahead + 1):
            start_date = month_start(reference_date, offset)
            end_date = month_start(reference_date, offset + 1)
            specs.append(
                PartitionSpec(
                    parent_table=parent_table,
                    partition_table=f"{parent_table}_p{start_date:%Y%m}",
                    start_date=start_date,
                    end_date=end_date,
                )
            )
    return tuple(specs)


def planned_partitions_for_range(start_date: date, end_date: date) -> tuple[PartitionSpec, ...]:
    """Plan every monthly partition touched by an inclusive historical range."""
    if end_date < start_date:
        raise ValueError("end_date must not be earlier than start_date")

    first_month = month_start(start_date)
    last_month = month_start(end_date)
    month_count = (last_month.year - first_month.year) * 12 + last_month.month - first_month.month
    return planned_partitions(first_month, months_ahead=month_count)


def planned_partitions_for_table(
    parent_table: str,
    business_dates: tuple[date, ...],
) -> tuple[PartitionSpec, ...]:
    """Plan only the partitions touched by one write to a known parent table."""
    if parent_table not in PARTITIONED_TABLES:
        raise ValueError(f"{parent_table} is not a managed partitioned table")

    return tuple(
        PartitionSpec(
            parent_table=parent_table,
            partition_table=f"{parent_table}_p{start_date:%Y%m}",
            start_date=start_date,
            end_date=month_start(start_date, 1),
        )
        for start_date in sorted({month_start(value) for value in business_dates})
    )


def ensure_monthly_partitions(
    connection: Connection,
    *,
    reference_date: date,
    months_ahead: int,
) -> tuple[str, ...]:
    """Ensure the current and future monthly partitions exist for fixed parent tables."""

    partition_names: list[str] = []
    for spec in planned_partitions(reference_date, months_ahead):
        connection.exec_driver_sql(spec.create_sql())
        partition_names.append(spec.partition_table)
    return tuple(partition_names)


def ensure_partitions_for_range(
    connection: Connection,
    *,
    start_date: date,
    end_date: date,
) -> tuple[str, ...]:
    """Ensure all monthly partitions needed by a historical backfill exist."""
    partition_names: list[str] = []
    for spec in planned_partitions_for_range(start_date, end_date):
        connection.exec_driver_sql(spec.create_sql())
        partition_names.append(spec.partition_table)
    return tuple(partition_names)


def ensure_partitions_for_write(
    connection: Connection,
    *,
    parent_table: str,
    business_dates: tuple[date, ...],
) -> tuple[str, ...]:
    """Ensure the exact monthly partitions needed by a formal-table write."""
    partition_names: list[str] = []
    for spec in planned_partitions_for_table(parent_table, business_dates):
        connection.exec_driver_sql(spec.create_sql())
        partition_names.append(spec.partition_table)
    return tuple(partition_names)
