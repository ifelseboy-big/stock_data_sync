from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Etf(Base):
    __tablename__ = "etf"
    __table_args__ = (CheckConstraint("list_status IN ('L', 'D', 'P')", name="list_status"),)

    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    csname: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extname: Mapped[str | None] = mapped_column(String(96), nullable=True)
    cname: Mapped[str | None] = mapped_column(String(192), nullable=True)
    index_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    index_name: Mapped[str | None] = mapped_column(String(192), nullable=True)
    setup_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    list_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    list_status: Mapped[str] = mapped_column(String(1), nullable=False)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    source_exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    mgr_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    custod_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    mgt_fee: Mapped[Decimal | None] = mapped_column(Numeric(14, 8), nullable=True)
    etf_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EtfDaily(Base):
    __tablename__ = "etf_daily"
    __table_args__ = (
        Index("idx_etf_daily_trade_code", "trade_date", "ts_code"),
        {"postgresql_partition_by": "RANGE (trade_date)"},
    )

    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("etf.ts_code"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    pre_close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    change: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    pct_chg: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    adj_factor: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EtfShareSizeDaily(Base):
    __tablename__ = "etf_share_size_daily"
    __table_args__ = (
        Index("idx_etf_share_trade_code", "trade_date", "ts_code"),
        {"postgresql_partition_by": "RANGE (trade_date)"},
    )

    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("etf.ts_code"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    etf_name: Mapped[str | None] = mapped_column(String(96), nullable=True)
    total_share: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    total_size: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    nav: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
