from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MarketIndex(Base):
    __tablename__ = "market_index"

    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    fullname: Mapped[str | None] = mapped_column(String(256), nullable=True)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(128), nullable=True)
    index_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    base_point: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    list_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    weight_rule: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    exp_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketIndexDaily(Base):
    __tablename__ = "market_index_daily"
    __table_args__ = (Index("idx_market_index_daily_trade_code", "trade_date", "ts_code"),)

    ts_code: Mapped[str] = mapped_column(
        String(20), ForeignKey("market_index.ts_code"), primary_key=True
    )
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    open: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    high: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    low: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    change: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pct_chg: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IndexDailyBasic(Base):
    __tablename__ = "index_daily_basic"

    ts_code: Mapped[str] = mapped_column(
        String(20), ForeignKey("market_index.ts_code"), primary_key=True
    )
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    total_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    float_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    total_share: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    float_share: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    free_share: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    turnover_rate_f: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    pe: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pe_ttm: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pb: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketIndexWeight(Base):
    __tablename__ = "market_index_weight"
    __table_args__ = (Index("idx_index_weight_member", "con_code", "snapshot_date", "index_code"),)

    index_code: Mapped[str] = mapped_column(
        String(20), ForeignKey("market_index.ts_code"), primary_key=True
    )
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    con_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), primary_key=True)
    weight: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
