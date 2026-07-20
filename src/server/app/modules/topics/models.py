from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    desc,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ConceptBoard(Base):
    __tablename__ = "concept_board"

    source: Mapped[str] = mapped_column(
        String(8), primary_key=True, default="THS", server_default="THS"
    )
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    member_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(8), nullable=True)
    list_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    board_type: Mapped[str] = mapped_column(String(8), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConceptBoardDaily(Base):
    __tablename__ = "concept_board_daily"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source", "ts_code"],
            ["concept_board.source", "concept_board.ts_code"],
            name="fk_concept_daily_board",
        ),
        Index("idx_concept_daily_trade_board", "trade_date", "source", "ts_code"),
    )

    source: Mapped[str] = mapped_column(
        String(8), primary_key=True, default="THS", server_default="THS"
    )
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    open: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    high: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    low: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    avg_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    change: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    total_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    float_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConceptBoardMember(Base):
    __tablename__ = "concept_board_member"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source", "ts_code"],
            ["concept_board.source", "concept_board.ts_code"],
            name="fk_concept_member_board",
        ),
        Index("idx_concept_member_stock", "con_code", "source", "ts_code"),
    )

    source: Mapped[str] = mapped_column(
        String(8), primary_key=True, default="THS", server_default="THS"
    )
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    con_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), primary_key=True)
    con_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(14, 8), nullable=True)
    in_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    out_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[date] = mapped_column(Date, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ThemeIndex(Base):
    __tablename__ = "theme_index"
    __table_args__ = (
        CheckConstraint("source = 'THS'", name="source"),
        CheckConstraint("theme_type = 'TH'", name="theme_type"),
    )

    source: Mapped[str] = mapped_column(
        String(8), primary_key=True, default="THS", server_default="THS"
    )
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    member_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(8), nullable=True)
    list_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    theme_type: Mapped[str] = mapped_column(String(8), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ThemeIndexDaily(Base):
    __tablename__ = "theme_index_daily"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source", "ts_code"],
            ["theme_index.source", "theme_index.ts_code"],
            name="fk_theme_index_daily_index",
        ),
        Index("idx_theme_index_daily_trade", "trade_date", "source", "ts_code"),
    )

    source: Mapped[str] = mapped_column(
        String(8), primary_key=True, default="THS", server_default="THS"
    )
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    open: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    high: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    low: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    avg_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    change: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    total_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    float_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ThemeIndexMember(Base):
    __tablename__ = "theme_index_member"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source", "ts_code"],
            ["theme_index.source", "theme_index.ts_code"],
            name="fk_theme_index_member_index",
        ),
        Index("idx_theme_index_member_stock", "con_code", "source", "ts_code"),
    )

    source: Mapped[str] = mapped_column(
        String(8), primary_key=True, default="THS", server_default="THS"
    )
    ts_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    con_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), primary_key=True)
    con_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(14, 8), nullable=True)
    in_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    out_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[date] = mapped_column(Date, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StockHotRankDaily(Base):
    __tablename__ = "stock_hot_rank_daily"
    __table_args__ = (
        CheckConstraint("source IN ('THS', 'DC')", name="source"),
        CheckConstraint("rank > 0", name="positive_rank"),
        UniqueConstraint(
            "source",
            "trade_date",
            "market_type",
            "rank_type",
            "rank",
            name="uq_hot_rank_position",
        ),
        Index(
            "idx_hot_rank_stock_history",
            "ts_code",
            "trade_date",
            "source",
            "rank_type",
        ),
        Index("idx_hot_rank_concept_gin", "concept", postgresql_using="gin"),
    )

    source: Mapped[str] = mapped_column(String(8), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    market_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    rank_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    data_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), primary_key=True)
    ts_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    concept: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB, nullable=True)
    rank_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    hot: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    rank_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketThemeDaily(Base):
    __tablename__ = "market_theme_daily"
    __table_args__ = (Index("idx_theme_daily_trade_rank", "trade_date", "source", "rank"),)

    source: Mapped[str] = mapped_column(
        String(8), primary_key=True, default="DC", server_default="DC"
    )
    theme_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    hot: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strength: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    z_t_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    main_change: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    lead_stock: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lead_stock_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lead_stock_pct_change: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketThemeMemberDaily(Base):
    __tablename__ = "market_theme_member_daily"
    __table_args__ = (
        Index("idx_theme_member_stock", "trade_date", "ts_code", "theme_code"),
        {"postgresql_partition_by": "RANGE (trade_date)"},
    )

    source: Mapped[str] = mapped_column(
        String(8), primary_key=True, default="DC", server_default="DC"
    )
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    theme_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    hot_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StockTopListDaily(Base):
    __tablename__ = "stock_top_list_daily"
    __table_args__ = (
        UniqueConstraint("trade_date", "ts_code", "reason", name="uq_top_list_day_stock_reason"),
        Index("idx_top_list_stock_history", "ts_code", "trade_date"),
    )

    top_list_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), nullable=False)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    l_sell: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    l_buy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    l_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    net_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    amount_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    float_values: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StockTopInstDaily(Base):
    __tablename__ = "stock_top_inst_daily"
    __table_args__ = (
        CheckConstraint("side IN (0, 1)", name="side"),
        Index("idx_top_inst_trade_stock", "trade_date", "ts_code"),
        Index("idx_top_inst_stock_history", "ts_code", "trade_date"),
        Index("idx_top_inst_exalter_history", "exalter", "trade_date"),
    )

    detail_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), nullable=False)
    exalter: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    buy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    buy_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    sell: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    sell_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    net_buy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StockLimitEventDaily(Base):
    __tablename__ = "stock_limit_event_daily"
    __table_args__ = (
        CheckConstraint("limit_type IN ('U', 'D', 'Z')", name="limit_type"),
        Index("idx_limit_event_stock_history", "ts_code", "trade_date", "limit_type"),
        Index("idx_limit_event_day_type", "trade_date", "limit_type", "ts_code"),
    )

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), primary_key=True)
    limit_type: Mapped[str] = mapped_column(String(1), primary_key=True)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pct_chg: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    amount_raw: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    limit_amount_raw: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    float_mv_raw: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    total_mv_raw: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    turnover_ratio: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    fd_amount_raw: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    first_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    last_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    open_times: Mapped[int | None] = mapped_column(Integer, nullable=True)
    up_stat: Mapped[str | None] = mapped_column(String(32), nullable=True)
    limit_times: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StockLimitStepDaily(Base):
    __tablename__ = "stock_limit_step_daily"
    __table_args__ = (
        CheckConstraint("nums > 0", name="positive_nums"),
        Index("idx_limit_step_day_nums", "trade_date", desc("nums"), "ts_code"),
    )

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nums: Mapped[int] = mapped_column(Integer, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
