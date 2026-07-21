from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from sqlalchemy import SQLColumnExpression

from app.modules.stocks.models import Stock, StockDaily, StockMoneyflowDaily, StockTechnicalDaily


@dataclass(frozen=True, slots=True)
class ScreenField:
    expression: SQLColumnExpression[Any]
    value_type: Literal["string", "number", "integer"]
    description: str
    nullable: bool = True
    unit: str | None = None
    unit_status: Literal["normalized", "dimensionless", "code"] = "dimensionless"


SCREEN_FIELDS: Final[dict[str, ScreenField]] = {
    "stock.ts_code": ScreenField(Stock.ts_code, "string", "股票代码", False),
    "stock.name": ScreenField(Stock.name, "string", "股票名称", False),
    "stock.area": ScreenField(Stock.area, "string", "地区"),
    "stock.industry": ScreenField(Stock.industry, "string", "行业"),
    "stock.market": ScreenField(Stock.market, "string", "市场板块"),
    "stock.exchange": ScreenField(Stock.exchange, "string", "交易所", False),
    "stock.list_status": ScreenField(Stock.list_status, "string", "上市状态", False),
    "stock.is_hs": ScreenField(Stock.is_hs, "string", "沪深港通标识"),
    "market.open": ScreenField(StockDaily.open, "number", "开盘价", False, "CNY", "normalized"),
    "market.high": ScreenField(StockDaily.high, "number", "最高价", False, "CNY", "normalized"),
    "market.low": ScreenField(StockDaily.low, "number", "最低价", False, "CNY", "normalized"),
    "market.close": ScreenField(StockDaily.close, "number", "收盘价", False, "CNY", "normalized"),
    "market.pct_chg": ScreenField(
        StockDaily.pct_chg, "number", "涨跌幅", False, "percent", "normalized"
    ),
    "market.volume": ScreenField(
        StockDaily.volume, "number", "成交量", False, "share", "normalized"
    ),
    "market.amount": ScreenField(StockDaily.amount, "number", "成交额", False, "CNY", "normalized"),
    "market.turnover_rate": ScreenField(
        StockDaily.turnover_rate, "number", "换手率", True, "percent", "normalized"
    ),
    "market.turnover_rate_f": ScreenField(
        StockDaily.turnover_rate_f, "number", "自由流通换手率", True, "percent", "normalized"
    ),
    "market.volume_ratio": ScreenField(StockDaily.volume_ratio, "number", "量比"),
    "market.limit_status": ScreenField(
        StockDaily.limit_status, "integer", "涨跌停状态", True, None, "code"
    ),
    "valuation.pe": ScreenField(StockDaily.pe, "number", "市盈率"),
    "valuation.pe_ttm": ScreenField(StockDaily.pe_ttm, "number", "滚动市盈率"),
    "valuation.pb": ScreenField(StockDaily.pb, "number", "市净率"),
    "valuation.ps": ScreenField(StockDaily.ps, "number", "市销率"),
    "valuation.ps_ttm": ScreenField(StockDaily.ps_ttm, "number", "滚动市销率"),
    "valuation.dv_ratio": ScreenField(
        StockDaily.dv_ratio, "number", "股息率", True, "percent", "normalized"
    ),
    "valuation.dv_ttm": ScreenField(
        StockDaily.dv_ttm, "number", "滚动股息率", True, "percent", "normalized"
    ),
    "valuation.total_mv": ScreenField(
        StockDaily.total_mv, "number", "总市值", True, "CNY", "normalized"
    ),
    "valuation.circ_mv": ScreenField(
        StockDaily.circ_mv, "number", "流通市值", True, "CNY", "normalized"
    ),
    "technical.macd_dif": ScreenField(
        StockTechnicalDaily.macd_dif, "number", "MACD DIF", True, "CNY", "normalized"
    ),
    "technical.macd_dea": ScreenField(
        StockTechnicalDaily.macd_dea, "number", "MACD DEA", True, "CNY", "normalized"
    ),
    "technical.macd": ScreenField(
        StockTechnicalDaily.macd, "number", "MACD柱", True, "CNY", "normalized"
    ),
    "technical.kdj_k": ScreenField(StockTechnicalDaily.kdj_k, "number", "KDJ K"),
    "technical.kdj_d": ScreenField(StockTechnicalDaily.kdj_d, "number", "KDJ D"),
    "technical.kdj_j": ScreenField(StockTechnicalDaily.kdj_j, "number", "KDJ J"),
    "technical.rsi_6": ScreenField(StockTechnicalDaily.rsi_6, "number", "RSI 6"),
    "technical.rsi_12": ScreenField(StockTechnicalDaily.rsi_12, "number", "RSI 12"),
    "technical.rsi_24": ScreenField(StockTechnicalDaily.rsi_24, "number", "RSI 24"),
    "technical.boll_upper": ScreenField(
        StockTechnicalDaily.boll_upper, "number", "布林线上轨", True, "CNY", "normalized"
    ),
    "technical.boll_mid": ScreenField(
        StockTechnicalDaily.boll_mid, "number", "布林线中轨", True, "CNY", "normalized"
    ),
    "technical.boll_lower": ScreenField(
        StockTechnicalDaily.boll_lower, "number", "布林线下轨", True, "CNY", "normalized"
    ),
    "technical.cci": ScreenField(StockTechnicalDaily.cci, "number", "CCI"),
    "moneyflow.net_mf_vol": ScreenField(
        StockMoneyflowDaily.net_mf_vol, "number", "净流入量", True, "share", "normalized"
    ),
    "moneyflow.net_mf_amount": ScreenField(
        StockMoneyflowDaily.net_mf_amount, "number", "净流入额", True, "CNY", "normalized"
    ),
    "moneyflow.buy_lg_amount": ScreenField(
        StockMoneyflowDaily.buy_lg_amount, "number", "大单买入额", True, "CNY", "normalized"
    ),
    "moneyflow.sell_lg_amount": ScreenField(
        StockMoneyflowDaily.sell_lg_amount, "number", "大单卖出额", True, "CNY", "normalized"
    ),
    "moneyflow.buy_elg_amount": ScreenField(
        StockMoneyflowDaily.buy_elg_amount, "number", "特大单买入额", True, "CNY", "normalized"
    ),
    "moneyflow.sell_elg_amount": ScreenField(
        StockMoneyflowDaily.sell_elg_amount, "number", "特大单卖出额", True, "CNY", "normalized"
    ),
}

DEFAULT_SCREEN_FIELDS: Final[tuple[str, ...]] = (
    "stock.ts_code",
    "stock.name",
    "stock.industry",
    "market.close",
    "market.pct_chg",
    "market.amount",
    "market.turnover_rate",
    "valuation.pe_ttm",
    "valuation.pb",
    "valuation.total_mv",
)


def coerce_screen_value(field: ScreenField, value: str | int | Decimal) -> str | int | Decimal:
    if field.value_type == "string":
        return str(value)
    if field.value_type == "integer":
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"expected integer, got {value!r}") from exc
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"expected number, got {value!r}") from exc
