from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    desc,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TradeCalendar(Base):
    __tablename__ = "trade_calendar"

    exchange: Mapped[str] = mapped_column(String(8), primary_key=True)
    cal_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pretrade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Stock(Base):
    __tablename__ = "stock"
    __table_args__ = (
        CheckConstraint("list_status IN ('L', 'D', 'P', 'G')", name="list_status"),
        CheckConstraint("is_hs IS NULL OR is_hs IN ('N', 'H', 'S')", name="is_hs"),
        Index("uq_stock_exchange_symbol", "exchange", "symbol", unique=True),
    )

    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    area: Mapped[str | None] = mapped_column(String(32), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fullname: Mapped[str | None] = mapped_column(String(160), nullable=True)
    enname: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cnspell: Mapped[str | None] = mapped_column(String(32), nullable=True)
    market: Mapped[str | None] = mapped_column(String(16), nullable=True)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    curr_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    list_status: Mapped[str] = mapped_column(String(1), nullable=False)
    list_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delist_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_hs: Mapped[str | None] = mapped_column(String(1), nullable=True)
    act_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    act_ent_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StockCompany(Base):
    __tablename__ = "stock_company"

    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), primary_key=True)
    com_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    com_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(8), nullable=True)
    chairman: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manager: Mapped[str | None] = mapped_column(String(64), nullable=True)
    secretary: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reg_capital: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    setup_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    province: Mapped[str | None] = mapped_column(String(32), nullable=True)
    city: Mapped[str | None] = mapped_column(String(32), nullable=True)
    introduction: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(String(256), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    office: Mapped[str | None] = mapped_column(Text, nullable=True)
    employees: Mapped[int | None] = mapped_column(Integer, nullable=True)
    main_business: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StockDaily(Base):
    __tablename__ = "stock_daily"
    __table_args__ = (
        CheckConstraint(
            "limit_status IS NULL OR limit_status BETWEEN 0 AND 6", name="limit_status"
        ),
        Index("idx_stock_daily_trade_code", "trade_date", "ts_code"),
        {"postgresql_partition_by": "RANGE (trade_date)"},
    )

    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), primary_key=True)
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
    after_hours_volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    after_hours_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    adj_factor: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    turnover_rate_f: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    volume_ratio: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    pe: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pe_ttm: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pb: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ps: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ps_ttm: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    dv_ratio: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    dv_ttm: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    total_share: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    float_share: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    free_share: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    total_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    circ_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    limit_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    up_limit: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    down_limit: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StockTechnicalDaily(Base):
    __tablename__ = "stock_technical_daily"
    __table_args__ = (
        Index("idx_stock_technical_trade_code", "trade_date", "ts_code"),
        {"postgresql_partition_by": "RANGE (trade_date)"},
    )

    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open_hfq: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    open_qfq: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    close_hfq: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    close_qfq: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    high_hfq: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    high_qfq: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    low_hfq: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    low_qfq: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pre_close_hfq: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pre_close_qfq: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    macd_dif: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    macd_dea: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    macd: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    kdj_k: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    kdj_d: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    kdj_j: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    rsi_6: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    rsi_12: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    rsi_24: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    boll_upper: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    boll_mid: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    boll_lower: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    cci: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StockMoneyflowDaily(Base):
    __tablename__ = "stock_moneyflow_daily"
    __table_args__ = (
        Index("idx_stock_moneyflow_trade_code", "trade_date", "ts_code"),
        {"postgresql_partition_by": "RANGE (trade_date)"},
    )

    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    buy_sm_vol: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    sell_sm_vol: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    buy_md_vol: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    sell_md_vol: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    buy_lg_vol: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    sell_lg_vol: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    buy_elg_vol: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    sell_elg_vol: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    net_mf_vol: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    buy_sm_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    sell_sm_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    buy_md_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    sell_md_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    buy_lg_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    sell_lg_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    buy_elg_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    sell_elg_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    net_mf_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ThsBoardMoneyflowDaily(Base):
    __tablename__ = "ths_board_moneyflow_daily"
    __table_args__ = (
        CheckConstraint("board_type IN ('CONCEPT', 'INDUSTRY')", name="board_type"),
        Index(
            "idx_ths_board_flow_day_amount",
            "trade_date",
            "board_type",
            desc("net_amount"),
        ),
        Index("idx_ths_board_flow_code", "ts_code", "trade_date"),
    )

    board_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    board_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    ts_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lead_stock: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lead_stock_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    board_index: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    company_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lead_stock_pct_change: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    net_buy_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    net_sell_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StockSuspendDaily(Base):
    __tablename__ = "stock_suspend_daily"
    __table_args__ = (
        CheckConstraint("suspend_type IN ('S', 'R')", name="suspend_type"),
        Index("idx_suspend_day_type", "trade_date", "suspend_type", "ts_code"),
    )

    ts_code: Mapped[str] = mapped_column(String(16), ForeignKey("stock.ts_code"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    suspend_type: Mapped[str] = mapped_column(String(1), primary_key=True)
    suspend_timing: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
