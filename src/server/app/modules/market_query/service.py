from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import and_, asc, desc, distinct, func, not_, or_, select
from sqlalchemy.sql import ColumnElement, Select

from app.mcp.database import McpReadOnlyQuery
from app.modules.etfs.models import Etf, EtfDaily, EtfShareSizeDaily
from app.modules.indices.models import (
    IndexDailyBasic,
    MarketIndex,
    MarketIndexDaily,
    MarketIndexWeight,
)
from app.modules.market_query.fields import (
    DEFAULT_SCREEN_FIELDS,
    SCREEN_FIELDS,
    coerce_screen_value,
)
from app.modules.market_query.schemas import (
    ScreenFilter,
    ScreenOperator,
    ScreenSort,
    ScreenUniverse,
    SecurityType,
)
from app.modules.processing.models import DatasetRelease
from app.modules.stocks.models import (
    Stock,
    StockCompany,
    StockDaily,
    StockMoneyflowDaily,
    StockSuspendDaily,
    StockTechnicalDaily,
    ThsBoardMoneyflowDaily,
)
from app.modules.topics.models import (
    ConceptBoard,
    ConceptBoardDaily,
    ConceptBoardMember,
    MarketThemeDaily,
    MarketThemeMemberDaily,
    StockHotRankDaily,
    StockLimitEventDaily,
    StockLimitStepDaily,
    StockTopInstDaily,
    StockTopListDaily,
    ThemeIndex,
    ThemeIndexDaily,
    ThemeIndexMember,
)

JsonObject = dict[str, Any]
STOCK_UNIT_GROUPS: JsonObject = {
    "CNY": [
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "up_limit",
        "down_limit",
        "open_qfq",
        "high_qfq",
        "low_qfq",
        "close_qfq",
        "pre_close_qfq",
        "open_hfq",
        "high_hfq",
        "low_hfq",
        "close_hfq",
        "pre_close_hfq",
        "macd_dif",
        "macd_dea",
        "macd",
        "boll_upper",
        "boll_mid",
        "boll_lower",
        "amount",
        "total_mv",
        "circ_mv",
        "buy_sm_amount",
        "sell_sm_amount",
        "buy_md_amount",
        "sell_md_amount",
        "buy_lg_amount",
        "sell_lg_amount",
        "buy_elg_amount",
        "sell_elg_amount",
        "net_mf_amount",
    ],
    "share": ["volume", "total_share", "float_share", "free_share", "net_mf_vol"],
    "percent": ["pct_chg", "turnover_rate", "turnover_rate_f", "dv_ratio", "dv_ttm"],
    "dimensionless": [
        "adj_factor",
        "volume_ratio",
        "pe",
        "pe_ttm",
        "pb",
        "ps",
        "ps_ttm",
        "kdj_k",
        "kdj_d",
        "kdj_j",
        "rsi_6",
        "rsi_12",
        "rsi_24",
        "cci",
    ],
    "precision": "decimal_string",
}


class MarketQueryError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> JsonObject:
        return {"code": self.code, "message": self.message, "details": _json_value(self.details)}


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return value


def _success(data: Any, **meta: Any) -> JsonObject:
    return {"ok": True, "data": _json_value(data), "meta": _json_value(meta), "error": None}


def failure(exc: MarketQueryError) -> JsonObject:
    return {"ok": False, "data": None, "meta": {}, "error": exc.as_dict()}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _mapping_rows(result: Any) -> list[JsonObject]:
    return [dict(row) for row in result.mappings().all()]


class MarketQueryService:
    """Fixed, bounded, read-only market queries for the local MCP surface."""

    def __init__(self, query: McpReadOnlyQuery) -> None:
        self._query = query

    async def _resolve_date(
        self,
        datasets: Sequence[str],
        requested: date | None,
        *,
        scope_type: Literal["DATE", "MONTH"] = "DATE",
    ) -> date:
        required = tuple(dict.fromkeys(datasets))
        if not required:
            raise MarketQueryError("INVALID_REQUEST", "at least one dataset is required")
        statement = (
            select(DatasetRelease.business_date)
            .where(
                DatasetRelease.scope_type == scope_type,
                DatasetRelease.dataset_name.in_(required),
                DatasetRelease.business_date.is_not(None),
            )
            .group_by(DatasetRelease.business_date)
            .having(func.count(distinct(DatasetRelease.dataset_name)) == len(required))
            .order_by(desc(DatasetRelease.business_date))
            .limit(1)
        )
        if requested is not None:
            statement = statement.where(DatasetRelease.business_date == requested)
        resolved = (await self._query.execute(statement)).scalar_one_or_none()
        if resolved is None:
            raise MarketQueryError(
                "DATA_NOT_READY",
                "所需数据集尚未形成共同发布版本",
                datasets=list(required),
                requested_date=requested,
                scope_type=scope_type,
            )
        return cast(date, resolved)

    def _released_dates(self, datasets: Sequence[str]) -> Select[Any]:
        required = tuple(dict.fromkeys(datasets))
        return (
            select(DatasetRelease.business_date)
            .where(
                DatasetRelease.scope_type == "DATE",
                DatasetRelease.dataset_name.in_(required),
                DatasetRelease.business_date.is_not(None),
            )
            .group_by(DatasetRelease.business_date)
            .having(func.count(distinct(DatasetRelease.dataset_name)) == len(required))
        )

    async def _latest_release_at_or_before(
        self,
        dataset: str,
        requested: date | None,
        *,
        scope_type: Literal["DATE", "MONTH"],
    ) -> date:
        statement = select(func.max(DatasetRelease.business_date)).where(
            DatasetRelease.dataset_name == dataset,
            DatasetRelease.scope_type == scope_type,
            DatasetRelease.business_date.is_not(None),
        )
        if requested is not None:
            statement = statement.where(DatasetRelease.business_date <= requested)
        resolved = (await self._query.execute(statement)).scalar_one_or_none()
        if resolved is None:
            raise MarketQueryError(
                "DATA_NOT_READY",
                "指定范围内没有已发布的数据版本",
                dataset=dataset,
                requested_date=requested,
                scope_type=scope_type,
            )
        return cast(date, resolved)

    async def search_securities(
        self,
        keyword: str,
        security_types: Sequence[SecurityType] | None = None,
        *,
        limit: int = 20,
    ) -> JsonObject:
        term = keyword.strip()
        if not term:
            raise MarketQueryError("INVALID_REQUEST", "keyword cannot be empty")
        if len(term) > 128:
            raise MarketQueryError("INVALID_REQUEST", "keyword cannot exceed 128 characters")
        limit = min(max(limit, 1), 50)
        types = set(security_types or tuple(SecurityType))
        pattern = f"%{_escape_like(term)}%"
        prefix = f"{_escape_like(term)}%"
        results: list[JsonObject] = []

        if SecurityType.STOCK in types:
            stock_statement = (
                select(
                    Stock.ts_code.label("code"),
                    Stock.symbol,
                    Stock.name,
                    Stock.exchange,
                    Stock.market,
                    Stock.industry,
                    Stock.list_status,
                )
                .where(
                    or_(
                        Stock.ts_code.ilike(pattern, escape="\\"),
                        Stock.symbol.ilike(pattern, escape="\\"),
                        Stock.name.ilike(pattern, escape="\\"),
                        Stock.cnspell.ilike(pattern, escape="\\"),
                        Stock.fullname.ilike(pattern, escape="\\"),
                    )
                )
                .order_by(
                    desc(Stock.ts_code.ilike(prefix, escape="\\")),
                    desc(Stock.name.ilike(prefix, escape="\\")),
                    Stock.ts_code,
                )
                .limit(limit)
            )
            results.extend(
                {"type": "stock", **row}
                for row in _mapping_rows(await self._query.execute(stock_statement))
            )

        if SecurityType.ETF in types:
            etf_statement = (
                select(
                    Etf.ts_code.label("code"),
                    Etf.csname,
                    Etf.extname,
                    Etf.cname,
                    Etf.exchange,
                    Etf.index_code,
                    Etf.index_name,
                    Etf.etf_type,
                    Etf.list_status,
                )
                .where(
                    or_(
                        Etf.ts_code.ilike(pattern, escape="\\"),
                        Etf.csname.ilike(pattern, escape="\\"),
                        Etf.extname.ilike(pattern, escape="\\"),
                        Etf.cname.ilike(pattern, escape="\\"),
                        Etf.index_name.ilike(pattern, escape="\\"),
                    )
                )
                .order_by(desc(Etf.ts_code.ilike(prefix, escape="\\")), Etf.ts_code)
                .limit(limit)
            )
            for row in _mapping_rows(await self._query.execute(etf_statement)):
                row["name"] = row.get("csname") or row.get("extname") or row.get("cname")
                results.append({"type": "etf", **row})

        if SecurityType.INDEX in types:
            index_statement = (
                select(
                    MarketIndex.ts_code.label("code"),
                    MarketIndex.name,
                    MarketIndex.fullname,
                    MarketIndex.market,
                    MarketIndex.publisher,
                    MarketIndex.index_type,
                    MarketIndex.category,
                )
                .where(
                    or_(
                        MarketIndex.ts_code.ilike(pattern, escape="\\"),
                        MarketIndex.name.ilike(pattern, escape="\\"),
                        MarketIndex.fullname.ilike(pattern, escape="\\"),
                    )
                )
                .order_by(desc(MarketIndex.ts_code.ilike(prefix, escape="\\")), MarketIndex.ts_code)
                .limit(limit)
            )
            results.extend(
                {"type": "index", **row}
                for row in _mapping_rows(await self._query.execute(index_statement))
            )

        lowered = term.casefold()

        def score(item: JsonObject) -> tuple[int, str, str]:
            code = str(item.get("code") or "").casefold()
            name = str(item.get("name") or "").casefold()
            rank = (
                0
                if code == lowered
                else 1
                if code.startswith(lowered)
                else 2
                if name.startswith(lowered)
                else 3
            )
            return rank, code, str(item["type"])

        results.sort(key=score)
        return _success(
            results[:limit], keyword=term, returned=min(len(results), limit), limit=limit
        )

    async def get_stock_snapshot(
        self,
        ts_code: str,
        trade_date: date | None = None,
    ) -> JsonObject:
        datasets = (
            "stock_daily.core",
            "stock_daily.limit",
            "stock_technical_daily",
            "stock_moneyflow_daily",
        )
        resolved = await self._resolve_date(datasets, trade_date)
        statement = (
            select(
                Stock.ts_code,
                Stock.symbol,
                Stock.name,
                Stock.area,
                Stock.industry,
                Stock.market,
                Stock.exchange,
                Stock.list_status,
                Stock.list_date,
                Stock.is_hs,
                Stock.act_name,
                Stock.act_ent_type,
                StockCompany.com_name,
                StockCompany.chairman,
                StockCompany.manager,
                StockCompany.province,
                StockCompany.city,
                StockCompany.website,
                StockCompany.employees,
                StockCompany.main_business,
                StockDaily.trade_date,
                StockDaily.open,
                StockDaily.high,
                StockDaily.low,
                StockDaily.close,
                StockDaily.pre_close,
                StockDaily.change,
                StockDaily.pct_chg,
                StockDaily.volume,
                StockDaily.amount,
                StockDaily.adj_factor,
                StockDaily.turnover_rate,
                StockDaily.turnover_rate_f,
                StockDaily.volume_ratio,
                StockDaily.pe,
                StockDaily.pe_ttm,
                StockDaily.pb,
                StockDaily.ps,
                StockDaily.ps_ttm,
                StockDaily.dv_ratio,
                StockDaily.dv_ttm,
                StockDaily.total_share,
                StockDaily.float_share,
                StockDaily.free_share,
                StockDaily.total_mv,
                StockDaily.circ_mv,
                StockDaily.limit_status,
                StockDaily.up_limit,
                StockDaily.down_limit,
                StockTechnicalDaily.open_qfq,
                StockTechnicalDaily.high_qfq,
                StockTechnicalDaily.low_qfq,
                StockTechnicalDaily.close_qfq,
                StockTechnicalDaily.open_hfq,
                StockTechnicalDaily.high_hfq,
                StockTechnicalDaily.low_hfq,
                StockTechnicalDaily.close_hfq,
                StockTechnicalDaily.macd_dif,
                StockTechnicalDaily.macd_dea,
                StockTechnicalDaily.macd,
                StockTechnicalDaily.kdj_k,
                StockTechnicalDaily.kdj_d,
                StockTechnicalDaily.kdj_j,
                StockTechnicalDaily.rsi_6,
                StockTechnicalDaily.rsi_12,
                StockTechnicalDaily.rsi_24,
                StockTechnicalDaily.boll_upper,
                StockTechnicalDaily.boll_mid,
                StockTechnicalDaily.boll_lower,
                StockTechnicalDaily.cci,
                StockMoneyflowDaily.buy_sm_amount,
                StockMoneyflowDaily.sell_sm_amount,
                StockMoneyflowDaily.buy_md_amount,
                StockMoneyflowDaily.sell_md_amount,
                StockMoneyflowDaily.buy_lg_amount,
                StockMoneyflowDaily.sell_lg_amount,
                StockMoneyflowDaily.buy_elg_amount,
                StockMoneyflowDaily.sell_elg_amount,
                StockMoneyflowDaily.net_mf_vol,
                StockMoneyflowDaily.net_mf_amount,
            )
            .select_from(Stock)
            .join(
                StockDaily,
                and_(StockDaily.ts_code == Stock.ts_code, StockDaily.trade_date == resolved),
            )
            .outerjoin(StockCompany, StockCompany.ts_code == Stock.ts_code)
            .join(
                StockTechnicalDaily,
                and_(
                    StockTechnicalDaily.ts_code == Stock.ts_code,
                    StockTechnicalDaily.trade_date == resolved,
                ),
            )
            .join(
                StockMoneyflowDaily,
                and_(
                    StockMoneyflowDaily.ts_code == Stock.ts_code,
                    StockMoneyflowDaily.trade_date == resolved,
                ),
            )
            .where(Stock.ts_code == ts_code.upper())
        )
        row = (await self._query.execute(statement)).mappings().one_or_none()
        if row is None:
            raise MarketQueryError(
                "NOT_FOUND",
                "指定股票在已发布日期没有完整快照",
                ts_code=ts_code,
                trade_date=resolved,
            )
        data = dict(row)
        profile_keys = (
            "ts_code",
            "symbol",
            "name",
            "area",
            "industry",
            "market",
            "exchange",
            "list_status",
            "list_date",
            "is_hs",
            "act_name",
            "act_ent_type",
            "com_name",
            "chairman",
            "manager",
            "province",
            "city",
            "website",
            "employees",
            "main_business",
        )
        technical_keys = (
            "open_qfq",
            "high_qfq",
            "low_qfq",
            "close_qfq",
            "open_hfq",
            "high_hfq",
            "low_hfq",
            "close_hfq",
            "macd_dif",
            "macd_dea",
            "macd",
            "kdj_k",
            "kdj_d",
            "kdj_j",
            "rsi_6",
            "rsi_12",
            "rsi_24",
            "boll_upper",
            "boll_mid",
            "boll_lower",
            "cci",
        )
        moneyflow_keys = (
            "buy_sm_amount",
            "sell_sm_amount",
            "buy_md_amount",
            "sell_md_amount",
            "buy_lg_amount",
            "sell_lg_amount",
            "buy_elg_amount",
            "sell_elg_amount",
            "net_mf_vol",
            "net_mf_amount",
        )
        return _success(
            {
                "profile": {key: data[key] for key in profile_keys},
                "quote": {
                    key: value
                    for key, value in data.items()
                    if key not in set(profile_keys) | set(technical_keys) | set(moneyflow_keys)
                },
                "technical": {key: data[key] for key in technical_keys},
                "moneyflow": {key: data[key] for key in moneyflow_keys},
            },
            trade_date=resolved,
            datasets=datasets,
            units=STOCK_UNIT_GROUPS,
        )

    async def get_stock_history(
        self,
        ts_code: str,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        adjustment: Literal["raw", "qfq", "hfq"] = "raw",
        include_moneyflow: bool = False,
        order: Literal["asc", "desc"] = "desc",
        limit: int = 250,
    ) -> JsonObject:
        if start_date and end_date and start_date > end_date:
            raise MarketQueryError("INVALID_REQUEST", "start_date cannot be later than end_date")
        limit = min(max(limit, 1), 1000)
        datasets = ["stock_daily.core"]
        if adjustment != "raw":
            datasets.append("stock_technical_daily")
        if include_moneyflow:
            datasets.append("stock_moneyflow_daily")
        price_columns: tuple[Any, ...]
        if adjustment == "raw":
            price_columns = (
                StockDaily.open,
                StockDaily.high,
                StockDaily.low,
                StockDaily.close,
                StockDaily.pre_close,
            )
        else:
            suffix = adjustment
            price_columns = tuple(
                getattr(StockTechnicalDaily, f"{name}_{suffix}").label(name)
                for name in ("open", "high", "low", "close", "pre_close")
            )
        change_column: Any = StockDaily.change
        if adjustment != "raw":
            adjusted_close = getattr(StockTechnicalDaily, f"close_{adjustment}")
            adjusted_pre_close = getattr(StockTechnicalDaily, f"pre_close_{adjustment}")
            change_column = (adjusted_close - adjusted_pre_close).label("change")
        columns: list[Any] = [
            StockDaily.trade_date,
            *price_columns,
            change_column,
            StockDaily.pct_chg,
            StockDaily.volume,
            StockDaily.amount,
            StockDaily.adj_factor,
            StockDaily.turnover_rate,
            StockDaily.turnover_rate_f,
            StockDaily.volume_ratio,
            StockDaily.pe,
            StockDaily.pe_ttm,
            StockDaily.pb,
            StockDaily.ps,
            StockDaily.ps_ttm,
            StockDaily.dv_ratio,
            StockDaily.dv_ttm,
            StockDaily.total_mv,
            StockDaily.circ_mv,
            StockDaily.limit_status,
            StockDaily.up_limit,
            StockDaily.down_limit,
        ]
        if include_moneyflow:
            columns.extend(
                (
                    StockMoneyflowDaily.net_mf_vol,
                    StockMoneyflowDaily.net_mf_amount,
                    StockMoneyflowDaily.buy_lg_amount,
                    StockMoneyflowDaily.sell_lg_amount,
                    StockMoneyflowDaily.buy_elg_amount,
                    StockMoneyflowDaily.sell_elg_amount,
                )
            )
        statement = select(*columns).select_from(StockDaily)
        if adjustment != "raw":
            statement = statement.join(
                StockTechnicalDaily,
                and_(
                    StockTechnicalDaily.ts_code == StockDaily.ts_code,
                    StockTechnicalDaily.trade_date == StockDaily.trade_date,
                ),
            )
        if include_moneyflow:
            statement = statement.join(
                StockMoneyflowDaily,
                and_(
                    StockMoneyflowDaily.ts_code == StockDaily.ts_code,
                    StockMoneyflowDaily.trade_date == StockDaily.trade_date,
                ),
            )
        statement = statement.where(
            StockDaily.ts_code == ts_code.upper(),
            StockDaily.trade_date.in_(self._released_dates(datasets)),
        )
        if start_date:
            statement = statement.where(StockDaily.trade_date >= start_date)
        if end_date:
            statement = statement.where(StockDaily.trade_date <= end_date)
        ordering = asc(StockDaily.trade_date) if order == "asc" else desc(StockDaily.trade_date)
        statement = statement.order_by(ordering).limit(limit)
        rows = _mapping_rows(await self._query.execute(statement))
        return _success(
            rows,
            ts_code=ts_code.upper(),
            adjustment=adjustment,
            datasets=datasets,
            returned=len(rows),
            limit=limit,
            order=order,
            adjustment_semantics=(
                "unadjusted prices"
                if adjustment == "raw"
                else (
                    "Tushare historical daily snapshot adjustment; not dynamically rebased to today"
                )
            ),
            units=STOCK_UNIT_GROUPS,
        )

    def list_screen_fields(self) -> JsonObject:
        fields = [
            {
                "field": name,
                "type": definition.value_type,
                "nullable": definition.nullable,
                "description": definition.description,
                "unit": definition.unit,
                "unit_status": definition.unit_status,
            }
            for name, definition in SCREEN_FIELDS.items()
        ]
        return _success(fields, operators=[item.value for item in ScreenOperator])

    @staticmethod
    def _screen_predicate(item: ScreenFilter) -> ColumnElement[bool]:
        definition = SCREEN_FIELDS.get(item.field)
        if definition is None:
            raise MarketQueryError("UNSUPPORTED_FIELD", "不支持的筛选字段", field=item.field)
        expression = definition.expression
        operator = item.operator
        if operator is ScreenOperator.IS_NULL:
            return expression.is_(None) if item.value else expression.is_not(None)
        try:
            if operator in {ScreenOperator.BETWEEN, ScreenOperator.IN}:
                assert isinstance(item.value, list)
                values = [coerce_screen_value(definition, value) for value in item.value]
                if operator is ScreenOperator.BETWEEN:
                    return expression.between(values[0], values[1])
                return expression.in_(values)
            assert item.value is not None and not isinstance(item.value, list)
            value = coerce_screen_value(definition, item.value)
        except ValueError as exc:
            raise MarketQueryError(
                "INVALID_FILTER_VALUE", str(exc), field=item.field, value=item.value
            ) from exc
        operations = {
            ScreenOperator.EQ: expression == value,
            ScreenOperator.NE: expression != value,
            ScreenOperator.GT: expression > value,
            ScreenOperator.GTE: expression >= value,
            ScreenOperator.LT: expression < value,
            ScreenOperator.LTE: expression <= value,
        }
        return operations[operator]

    async def screen_stocks(
        self,
        *,
        filters: Sequence[ScreenFilter] = (),
        match: Literal["all", "any"] = "all",
        universe: ScreenUniverse | None = None,
        fields: Sequence[str] | None = None,
        sort: Sequence[ScreenSort] = (),
        trade_date: date | None = None,
        limit: int = 50,
    ) -> JsonObject:
        if len(filters) > 20:
            raise MarketQueryError(
                "INVALID_REQUEST", "filters cannot contain more than 20 conditions"
            )
        if fields is not None and len(fields) > 50:
            raise MarketQueryError("INVALID_REQUEST", "fields cannot contain more than 50 items")
        if len(sort) > 5:
            raise MarketQueryError("INVALID_REQUEST", "sort cannot contain more than 5 items")
        selected_names = tuple(dict.fromkeys(fields or DEFAULT_SCREEN_FIELDS))
        unknown_fields = sorted(set(selected_names) - set(SCREEN_FIELDS))
        if unknown_fields:
            raise MarketQueryError("UNSUPPORTED_FIELD", "返回字段不受支持", fields=unknown_fields)
        for sort_item in sort:
            if sort_item.field not in SCREEN_FIELDS:
                raise MarketQueryError(
                    "UNSUPPORTED_FIELD", "排序字段不受支持", field=sort_item.field
                )
        universe = universe or ScreenUniverse()
        datasets = [
            "stock_daily.core",
            "stock_daily.limit",
            "stock_technical_daily",
            "stock_moneyflow_daily",
        ]
        if universe.exclude_suspended:
            datasets.append("stock_suspend_daily")
        resolved = await self._resolve_date(datasets, trade_date)
        limit = min(max(limit, 1), 200)
        selected_columns: list[Any] = [
            SCREEN_FIELDS[name].expression.label(name) for name in selected_names
        ]
        if "stock.ts_code" not in selected_names:
            selected_columns.insert(0, Stock.ts_code.label("stock.ts_code"))
        if "stock.name" not in selected_names:
            selected_columns.insert(1, Stock.name.label("stock.name"))
        statement = (
            select(*selected_columns)
            .select_from(Stock)
            .join(
                StockDaily,
                and_(StockDaily.ts_code == Stock.ts_code, StockDaily.trade_date == resolved),
            )
            .outerjoin(
                StockTechnicalDaily,
                and_(
                    StockTechnicalDaily.ts_code == Stock.ts_code,
                    StockTechnicalDaily.trade_date == resolved,
                ),
            )
            .outerjoin(
                StockMoneyflowDaily,
                and_(
                    StockMoneyflowDaily.ts_code == Stock.ts_code,
                    StockMoneyflowDaily.trade_date == resolved,
                ),
            )
        )
        conditions: list[ColumnElement[bool]] = []
        if universe.list_status:
            conditions.append(Stock.list_status.in_(universe.list_status))
        if universe.exchanges:
            conditions.append(Stock.exchange.in_(universe.exchanges))
        if universe.markets:
            conditions.append(Stock.market.in_(universe.markets))
        if universe.industries:
            conditions.append(Stock.industry.in_(universe.industries))
        if universe.is_hs:
            conditions.append(Stock.is_hs.in_(universe.is_hs))
        if universe.exclude_suspended:
            suspended = select(StockSuspendDaily.ts_code).where(
                StockSuspendDaily.ts_code == Stock.ts_code,
                StockSuspendDaily.trade_date == resolved,
                StockSuspendDaily.suspend_type == "S",
            )
            conditions.append(not_(suspended.exists()))
        if conditions:
            statement = statement.where(and_(*conditions))
        predicates = [self._screen_predicate(item) for item in filters]
        if predicates:
            statement = statement.where(and_(*predicates) if match == "all" else or_(*predicates))
        ordering: list[ColumnElement[Any]] = []
        for sort_item in sort:
            expression = SCREEN_FIELDS[sort_item.field].expression
            ordering.append(asc(expression) if sort_item.direction == "asc" else desc(expression))
        if not ordering:
            ordering.append(desc(StockDaily.total_mv))
        ordering.append(asc(Stock.ts_code))
        statement = statement.order_by(*ordering).limit(limit)
        rows = _mapping_rows(await self._query.execute(statement))
        return _success(
            rows,
            trade_date=resolved,
            datasets=datasets,
            returned=len(rows),
            limit=limit,
            match=match,
            field_units={name: SCREEN_FIELDS[name].unit for name in selected_names},
        )

    async def get_stock_events(
        self,
        ts_code: str,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        limit_per_type: int = 50,
    ) -> JsonObject:
        if start_date and end_date and start_date > end_date:
            raise MarketQueryError("INVALID_REQUEST", "start_date cannot be later than end_date")
        code = ts_code.upper()
        limit_per_type = min(max(limit_per_type, 1), 200)

        async def rows_for(
            statement: Select[Any],
            trade_column: Any,
            dataset: str,
        ) -> list[JsonObject]:
            statement = statement.where(trade_column.in_(self._released_dates((dataset,))))
            if start_date:
                statement = statement.where(trade_column >= start_date)
            if end_date:
                statement = statement.where(trade_column <= end_date)
            statement = statement.order_by(desc(trade_column)).limit(limit_per_type)
            return _mapping_rows(await self._query.execute(statement))

        hot = await rows_for(
            select(
                StockHotRankDaily.trade_date,
                StockHotRankDaily.source,
                StockHotRankDaily.market_type,
                StockHotRankDaily.rank_type,
                StockHotRankDaily.rank,
                StockHotRankDaily.pct_change,
                StockHotRankDaily.current_price,
                StockHotRankDaily.concept,
                StockHotRankDaily.rank_reason,
                StockHotRankDaily.hot,
                StockHotRankDaily.rank_time,
            ).where(StockHotRankDaily.ts_code == code),
            StockHotRankDaily.trade_date,
            "stock_hot_rank_daily",
        )
        top_list = await rows_for(
            select(
                StockTopListDaily.trade_date,
                StockTopListDaily.close,
                StockTopListDaily.pct_change,
                StockTopListDaily.turnover_rate,
                StockTopListDaily.amount,
                StockTopListDaily.l_sell,
                StockTopListDaily.l_buy,
                StockTopListDaily.net_amount,
                StockTopListDaily.net_rate,
                StockTopListDaily.reason,
            ).where(StockTopListDaily.ts_code == code),
            StockTopListDaily.trade_date,
            "stock_top_list_daily",
        )
        institutions = await rows_for(
            select(
                StockTopInstDaily.trade_date,
                StockTopInstDaily.exalter,
                StockTopInstDaily.side,
                StockTopInstDaily.buy,
                StockTopInstDaily.buy_rate,
                StockTopInstDaily.sell,
                StockTopInstDaily.sell_rate,
                StockTopInstDaily.net_buy,
                StockTopInstDaily.reason,
            ).where(StockTopInstDaily.ts_code == code),
            StockTopInstDaily.trade_date,
            "stock_top_inst_daily",
        )
        limit_events = await rows_for(
            select(
                StockLimitEventDaily.trade_date,
                StockLimitEventDaily.limit_type,
                StockLimitEventDaily.name,
                StockLimitEventDaily.industry,
                StockLimitEventDaily.close,
                StockLimitEventDaily.pct_chg,
                StockLimitEventDaily.amount_raw,
                StockLimitEventDaily.limit_amount_raw,
                StockLimitEventDaily.turnover_ratio,
                StockLimitEventDaily.fd_amount_raw,
                StockLimitEventDaily.first_time,
                StockLimitEventDaily.last_time,
                StockLimitEventDaily.open_times,
                StockLimitEventDaily.up_stat,
                StockLimitEventDaily.limit_times,
            ).where(StockLimitEventDaily.ts_code == code),
            StockLimitEventDaily.trade_date,
            "stock_limit_event_daily",
        )
        limit_steps = await rows_for(
            select(
                StockLimitStepDaily.trade_date,
                StockLimitStepDaily.name,
                StockLimitStepDaily.nums,
            ).where(StockLimitStepDaily.ts_code == code),
            StockLimitStepDaily.trade_date,
            "stock_limit_step_daily",
        )
        suspensions = await rows_for(
            select(
                StockSuspendDaily.trade_date,
                StockSuspendDaily.suspend_type,
                StockSuspendDaily.suspend_timing,
            ).where(StockSuspendDaily.ts_code == code),
            StockSuspendDaily.trade_date,
            "stock_suspend_daily",
        )
        concept_rows = _mapping_rows(
            await self._query.execute(
                select(
                    ConceptBoardMember.source,
                    ConceptBoardMember.ts_code.label("topic_code"),
                    ConceptBoard.name.label("topic_name"),
                    ConceptBoardMember.weight,
                    ConceptBoardMember.in_date,
                    ConceptBoardMember.out_date,
                    ConceptBoardMember.is_current,
                )
                .join(
                    ConceptBoard,
                    and_(
                        ConceptBoard.source == ConceptBoardMember.source,
                        ConceptBoard.ts_code == ConceptBoardMember.ts_code,
                    ),
                )
                .where(ConceptBoardMember.con_code == code)
                .order_by(desc(ConceptBoardMember.is_current), ConceptBoard.name)
                .limit(limit_per_type)
            )
        )
        theme_rows = _mapping_rows(
            await self._query.execute(
                select(
                    ThemeIndexMember.source,
                    ThemeIndexMember.ts_code.label("topic_code"),
                    ThemeIndex.name.label("topic_name"),
                    ThemeIndexMember.weight,
                    ThemeIndexMember.in_date,
                    ThemeIndexMember.out_date,
                    ThemeIndexMember.is_current,
                )
                .join(
                    ThemeIndex,
                    and_(
                        ThemeIndex.source == ThemeIndexMember.source,
                        ThemeIndex.ts_code == ThemeIndexMember.ts_code,
                    ),
                )
                .where(ThemeIndexMember.con_code == code)
                .order_by(desc(ThemeIndexMember.is_current), ThemeIndex.name)
                .limit(limit_per_type)
            )
        )
        market_theme_statement = (
            select(
                MarketThemeMemberDaily.trade_date,
                MarketThemeMemberDaily.source,
                MarketThemeMemberDaily.theme_code,
                MarketThemeDaily.name.label("topic_name"),
                MarketThemeMemberDaily.industry,
                MarketThemeMemberDaily.reason,
                MarketThemeMemberDaily.hot_num,
            )
            .join(
                MarketThemeDaily,
                and_(
                    MarketThemeDaily.source == MarketThemeMemberDaily.source,
                    MarketThemeDaily.trade_date == MarketThemeMemberDaily.trade_date,
                    MarketThemeDaily.theme_code == MarketThemeMemberDaily.theme_code,
                ),
            )
            .where(MarketThemeMemberDaily.ts_code == code)
        )
        market_themes = await rows_for(
            market_theme_statement,
            MarketThemeMemberDaily.trade_date,
            "market_theme_member_daily",
        )
        data = {
            "hot_rank": hot,
            "top_list": top_list,
            "institutions": institutions,
            "limit_events": limit_events,
            "limit_steps": limit_steps,
            "suspensions": suspensions,
            "concepts": concept_rows,
            "theme_indices": theme_rows,
            "market_themes": market_themes,
        }
        return _success(
            data,
            ts_code=code,
            limit_per_type=limit_per_type,
            units={
                "hot_rank": {
                    "current_price": "CNY",
                    "pct_change": "percent",
                    "hot": "provider_score",
                },
                "top_list": {
                    "amount/l_sell/l_buy/net_amount": "provider_original_amount_unit",
                    "pct_change/turnover_rate/net_rate": "percent",
                },
                "institutions": {
                    "buy/sell/net_buy": "CNY",
                    "buy_rate/sell_rate": "percent",
                },
                "limit_events": {
                    "close": "CNY",
                    "pct_chg/turnover_ratio": "percent",
                    "*_raw": "unknown_provider_unit",
                },
                "concepts/theme_indices": {"weight": "percent"},
            },
            raw_unit_fields=[
                "limit_events.amount_raw",
                "limit_events.limit_amount_raw",
                "limit_events.float_mv_raw",
                "limit_events.total_mv_raw",
                "limit_events.fd_amount_raw",
            ],
            raw_unit_status="unknown; do not aggregate or compare with normalized CNY fields",
        )

    async def get_market_rankings(
        self,
        kind: Literal["hot", "limit", "limit_step", "top_list", "board_moneyflow", "market_theme"],
        *,
        trade_date: date | None = None,
        source: str | None = None,
        rank_type: str | None = None,
        limit_type: Literal["U", "D", "Z"] | None = None,
        board_type: Literal["CONCEPT", "INDUSTRY"] | None = None,
        limit: int = 50,
    ) -> JsonObject:
        limit = min(max(limit, 1), 200)
        if kind == "hot":
            dataset = "stock_hot_rank_daily"
            resolved = await self._resolve_date((dataset,), trade_date)
            statement = select(
                StockHotRankDaily.source,
                StockHotRankDaily.trade_date,
                StockHotRankDaily.market_type,
                StockHotRankDaily.rank_type,
                StockHotRankDaily.ts_code,
                StockHotRankDaily.ts_name,
                StockHotRankDaily.rank,
                StockHotRankDaily.pct_change,
                StockHotRankDaily.current_price,
                StockHotRankDaily.concept,
                StockHotRankDaily.rank_reason,
                StockHotRankDaily.hot,
                StockHotRankDaily.rank_time,
            ).where(StockHotRankDaily.trade_date == resolved)
            if source:
                statement = statement.where(StockHotRankDaily.source == source.upper())
            if rank_type:
                statement = statement.where(StockHotRankDaily.rank_type == rank_type)
            statement = statement.order_by(
                StockHotRankDaily.source,
                StockHotRankDaily.market_type,
                StockHotRankDaily.rank_type,
                StockHotRankDaily.rank,
            )
        elif kind == "limit":
            dataset = "stock_limit_event_daily"
            resolved = await self._resolve_date((dataset,), trade_date)
            statement = select(
                StockLimitEventDaily.trade_date,
                StockLimitEventDaily.ts_code,
                StockLimitEventDaily.name,
                StockLimitEventDaily.industry,
                StockLimitEventDaily.limit_type,
                StockLimitEventDaily.close,
                StockLimitEventDaily.pct_chg,
                StockLimitEventDaily.amount_raw,
                StockLimitEventDaily.limit_amount_raw,
                StockLimitEventDaily.float_mv_raw,
                StockLimitEventDaily.total_mv_raw,
                StockLimitEventDaily.turnover_ratio,
                StockLimitEventDaily.fd_amount_raw,
                StockLimitEventDaily.first_time,
                StockLimitEventDaily.last_time,
                StockLimitEventDaily.open_times,
                StockLimitEventDaily.up_stat,
                StockLimitEventDaily.limit_times,
            ).where(StockLimitEventDaily.trade_date == resolved)
            if limit_type:
                statement = statement.where(StockLimitEventDaily.limit_type == limit_type)
            statement = statement.order_by(
                desc(StockLimitEventDaily.limit_times),
                desc(StockLimitEventDaily.fd_amount_raw),
                StockLimitEventDaily.ts_code,
            )
        elif kind == "limit_step":
            dataset = "stock_limit_step_daily"
            resolved = await self._resolve_date((dataset,), trade_date)
            statement = (
                select(
                    StockLimitStepDaily.trade_date,
                    StockLimitStepDaily.ts_code,
                    StockLimitStepDaily.name,
                    StockLimitStepDaily.nums,
                )
                .where(StockLimitStepDaily.trade_date == resolved)
                .order_by(desc(StockLimitStepDaily.nums), StockLimitStepDaily.ts_code)
            )
        elif kind == "top_list":
            dataset = "stock_top_list_daily"
            resolved = await self._resolve_date((dataset,), trade_date)
            statement = (
                select(
                    StockTopListDaily.trade_date,
                    StockTopListDaily.ts_code,
                    StockTopListDaily.name,
                    StockTopListDaily.close,
                    StockTopListDaily.pct_change,
                    StockTopListDaily.turnover_rate,
                    StockTopListDaily.amount,
                    StockTopListDaily.l_sell,
                    StockTopListDaily.l_buy,
                    StockTopListDaily.net_amount,
                    StockTopListDaily.net_rate,
                    StockTopListDaily.reason,
                )
                .where(StockTopListDaily.trade_date == resolved)
                .order_by(desc(StockTopListDaily.net_amount), StockTopListDaily.ts_code)
            )
        elif kind == "board_moneyflow":
            dataset = "ths_board_moneyflow_daily"
            resolved = await self._resolve_date((dataset,), trade_date)
            statement = select(
                ThsBoardMoneyflowDaily.trade_date,
                ThsBoardMoneyflowDaily.board_type,
                ThsBoardMoneyflowDaily.board_name,
                ThsBoardMoneyflowDaily.ts_code,
                ThsBoardMoneyflowDaily.lead_stock,
                ThsBoardMoneyflowDaily.lead_stock_price,
                ThsBoardMoneyflowDaily.pct_change,
                ThsBoardMoneyflowDaily.board_index,
                ThsBoardMoneyflowDaily.company_num,
                ThsBoardMoneyflowDaily.lead_stock_pct_change,
                ThsBoardMoneyflowDaily.net_buy_amount,
                ThsBoardMoneyflowDaily.net_sell_amount,
                ThsBoardMoneyflowDaily.net_amount,
            ).where(ThsBoardMoneyflowDaily.trade_date == resolved)
            if board_type:
                statement = statement.where(ThsBoardMoneyflowDaily.board_type == board_type)
            statement = statement.order_by(
                desc(ThsBoardMoneyflowDaily.net_amount), ThsBoardMoneyflowDaily.board_name
            )
        else:
            dataset = "market_theme_daily"
            resolved = await self._resolve_date((dataset,), trade_date)
            statement = select(
                MarketThemeDaily.trade_date,
                MarketThemeDaily.source,
                MarketThemeDaily.theme_code,
                MarketThemeDaily.name,
                MarketThemeDaily.pct_change,
                MarketThemeDaily.hot,
                MarketThemeDaily.rank,
                MarketThemeDaily.strength,
                MarketThemeDaily.z_t_num,
                MarketThemeDaily.main_change,
                MarketThemeDaily.lead_stock,
                MarketThemeDaily.lead_stock_code,
                MarketThemeDaily.lead_stock_pct_change,
            ).where(MarketThemeDaily.trade_date == resolved)
            if source:
                statement = statement.where(MarketThemeDaily.source == source.upper())
            statement = statement.order_by(
                MarketThemeDaily.rank, desc(MarketThemeDaily.hot), MarketThemeDaily.theme_code
            )
        statement = statement.limit(limit)
        rows = _mapping_rows(await self._query.execute(statement))
        extra_meta: JsonObject = {}
        ranking_units: JsonObject
        if kind == "hot":
            ranking_units = {
                "current_price": "CNY",
                "pct_change": "percent",
                "hot": "provider_score",
            }
        elif kind == "limit":
            ranking_units = {
                "close": "CNY",
                "pct_chg/turnover_ratio": "percent",
                "*_raw": "unknown_provider_unit",
            }
            extra_meta = {
                "raw_unit_fields": [
                    "amount_raw",
                    "limit_amount_raw",
                    "float_mv_raw",
                    "total_mv_raw",
                    "fd_amount_raw",
                ],
                "raw_unit_status": (
                    "unknown; do not aggregate or compare with normalized CNY fields"
                ),
            }
        elif kind == "limit_step":
            ranking_units = {"nums": "count"}
        elif kind == "top_list":
            ranking_units = {
                "amount/l_sell/l_buy/net_amount": "provider_original_amount_unit",
                "pct_change/turnover_rate/net_rate": "percent",
            }
        elif kind == "board_moneyflow":
            ranking_units = {
                "lead_stock_price": "CNY",
                "board_index": "index_point",
                "pct_change/lead_stock_pct_change": "percent",
                "net_buy_amount/net_sell_amount/net_amount": "CNY",
            }
        else:
            ranking_units = {
                "pct_change/lead_stock_pct_change": "percent",
                "main_change": "CNY",
                "hot/strength": "provider_score",
            }
        return _success(
            rows,
            kind=kind,
            trade_date=resolved,
            dataset=dataset,
            returned=len(rows),
            limit=limit,
            units=ranking_units,
            **extra_meta,
        )

    async def _resolve_unique_topic(
        self,
        exact_statement: Select[Any],
        fuzzy_statement: Select[Any],
        *,
        topic_label: str,
        identifier: str,
    ) -> JsonObject:
        exact_rows = _mapping_rows(await self._query.execute(exact_statement))
        if len(exact_rows) == 1:
            return exact_rows[0]
        candidates = exact_rows
        if not candidates:
            candidates = _mapping_rows(await self._query.execute(fuzzy_statement))
        if not candidates:
            raise MarketQueryError("NOT_FOUND", f"未找到{topic_label}", identifier=identifier)
        if len(candidates) > 1:
            candidate_fields = ("source", "ts_code", "theme_code", "name")
            raise MarketQueryError(
                "AMBIGUOUS_IDENTIFIER",
                f"{topic_label}名称匹配到多个结果，请改用精确代码",
                identifier=identifier,
                candidates=[
                    {key: row[key] for key in candidate_fields if key in row} for row in candidates
                ],
            )
        return candidates[0]

    async def get_topic(
        self,
        topic_type: Literal["concept", "theme_index", "market_theme"],
        identifier: str,
        *,
        trade_date: date | None = None,
        source: str | None = None,
        member_limit: int = 200,
    ) -> JsonObject:
        member_limit = min(max(member_limit, 1), 500)
        identifier = identifier.strip()
        if not identifier:
            raise MarketQueryError("INVALID_REQUEST", "identifier cannot be empty")
        if len(identifier) > 128:
            raise MarketQueryError("INVALID_REQUEST", "identifier cannot exceed 128 characters")
        pattern = f"%{_escape_like(identifier)}%"
        exact_pattern = _escape_like(identifier)
        if topic_type == "concept":
            source_name = (source or "THS").upper()
            concept_detail_columns = (
                ConceptBoard.source,
                ConceptBoard.ts_code,
                ConceptBoard.name,
                ConceptBoard.member_count,
                ConceptBoard.exchange,
                ConceptBoard.list_date,
                ConceptBoard.board_type,
            )
            detail = await self._resolve_unique_topic(
                select(*concept_detail_columns)
                .where(
                    ConceptBoard.source == source_name,
                    or_(
                        ConceptBoard.ts_code == identifier.upper(),
                        ConceptBoard.name.ilike(exact_pattern, escape="\\"),
                    ),
                )
                .order_by(ConceptBoard.ts_code)
                .limit(6),
                select(*concept_detail_columns)
                .where(
                    ConceptBoard.source == source_name,
                    ConceptBoard.name.ilike(pattern, escape="\\"),
                )
                .order_by(ConceptBoard.name, ConceptBoard.ts_code)
                .limit(6),
                topic_label="概念板块",
                identifier=identifier,
            )
            code = str(detail["ts_code"])
            if trade_date is None:
                members = _mapping_rows(
                    await self._query.execute(
                        select(
                            ConceptBoardMember.con_code.label("ts_code"),
                            ConceptBoardMember.con_name.label("name"),
                            ConceptBoardMember.weight,
                            ConceptBoardMember.is_current,
                            ConceptBoardMember.observed_at,
                        )
                        .where(
                            ConceptBoardMember.source == source_name,
                            ConceptBoardMember.ts_code == code,
                            ConceptBoardMember.is_current.is_(True),
                        )
                        .order_by(desc(ConceptBoardMember.weight), ConceptBoardMember.con_code)
                        .limit(member_limit)
                    )
                )
                observed_dates = [
                    row["observed_at"] for row in members if row.get("observed_at") is not None
                ]
                member_observed_at = max(observed_dates) if observed_dates else None
                member_basis = "current_snapshot"
            else:
                members = []
                member_observed_at = None
                member_basis = "unavailable_for_historical_date"
            daily: JsonObject | None = None
            try:
                resolved = await self._resolve_date(("concept_board_daily",), trade_date)
                daily_row = (
                    (
                        await self._query.execute(
                            select(
                                ConceptBoardDaily.trade_date,
                                ConceptBoardDaily.open,
                                ConceptBoardDaily.high,
                                ConceptBoardDaily.low,
                                ConceptBoardDaily.close,
                                ConceptBoardDaily.pre_close,
                                ConceptBoardDaily.avg_price,
                                ConceptBoardDaily.change,
                                ConceptBoardDaily.pct_change,
                                ConceptBoardDaily.volume,
                                ConceptBoardDaily.turnover_rate,
                                ConceptBoardDaily.total_mv,
                                ConceptBoardDaily.float_mv,
                            ).where(
                                ConceptBoardDaily.source == source_name,
                                ConceptBoardDaily.ts_code == code,
                                ConceptBoardDaily.trade_date == resolved,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                daily = dict(daily_row) if daily_row else None
            except MarketQueryError:
                if trade_date is not None:
                    raise
                resolved = None
            return _success(
                {"detail": dict(detail), "daily": daily, "members": members},
                topic_type=topic_type,
                trade_date=resolved,
                returned_members=len(members),
                member_basis=member_basis,
                member_observed_at=member_observed_at,
                units={
                    "daily.open/high/low/close/pre_close/avg_price/change": "index_point",
                    "daily.volume": "share",
                    "daily.pct_change/turnover_rate": "percent",
                    "daily.total_mv/float_mv": "CNY",
                    "members.weight": "percent",
                },
            )
        if topic_type == "theme_index":
            source_name = (source or "THS").upper()
            theme_detail_columns = (
                ThemeIndex.source,
                ThemeIndex.ts_code,
                ThemeIndex.name,
                ThemeIndex.member_count,
                ThemeIndex.exchange,
                ThemeIndex.list_date,
                ThemeIndex.theme_type,
            )
            detail = await self._resolve_unique_topic(
                select(*theme_detail_columns)
                .where(
                    ThemeIndex.source == source_name,
                    or_(
                        ThemeIndex.ts_code == identifier.upper(),
                        ThemeIndex.name.ilike(exact_pattern, escape="\\"),
                    ),
                )
                .order_by(ThemeIndex.ts_code)
                .limit(6),
                select(*theme_detail_columns)
                .where(
                    ThemeIndex.source == source_name,
                    ThemeIndex.name.ilike(pattern, escape="\\"),
                )
                .order_by(ThemeIndex.name, ThemeIndex.ts_code)
                .limit(6),
                topic_label="主题指数",
                identifier=identifier,
            )
            code = str(detail["ts_code"])
            if trade_date is None:
                members = _mapping_rows(
                    await self._query.execute(
                        select(
                            ThemeIndexMember.con_code.label("ts_code"),
                            ThemeIndexMember.con_name.label("name"),
                            ThemeIndexMember.weight,
                            ThemeIndexMember.is_current,
                            ThemeIndexMember.observed_at,
                        )
                        .where(
                            ThemeIndexMember.source == source_name,
                            ThemeIndexMember.ts_code == code,
                            ThemeIndexMember.is_current.is_(True),
                        )
                        .order_by(desc(ThemeIndexMember.weight), ThemeIndexMember.con_code)
                        .limit(member_limit)
                    )
                )
                observed_dates = [
                    row["observed_at"] for row in members if row.get("observed_at") is not None
                ]
                member_observed_at = max(observed_dates) if observed_dates else None
                member_basis = "current_snapshot"
            else:
                members = []
                member_observed_at = None
                member_basis = "unavailable_for_historical_date"
            try:
                resolved = await self._resolve_date(("theme_index_daily",), trade_date)
                daily_row = (
                    (
                        await self._query.execute(
                            select(
                                ThemeIndexDaily.trade_date,
                                ThemeIndexDaily.open,
                                ThemeIndexDaily.high,
                                ThemeIndexDaily.low,
                                ThemeIndexDaily.close,
                                ThemeIndexDaily.pre_close,
                                ThemeIndexDaily.avg_price,
                                ThemeIndexDaily.change,
                                ThemeIndexDaily.pct_change,
                                ThemeIndexDaily.volume,
                                ThemeIndexDaily.turnover_rate,
                                ThemeIndexDaily.total_mv,
                                ThemeIndexDaily.float_mv,
                            ).where(
                                ThemeIndexDaily.source == source_name,
                                ThemeIndexDaily.ts_code == code,
                                ThemeIndexDaily.trade_date == resolved,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                daily = dict(daily_row) if daily_row else None
            except MarketQueryError:
                if trade_date is not None:
                    raise
                resolved = None
                daily = None
            return _success(
                {"detail": dict(detail), "daily": daily, "members": members},
                topic_type=topic_type,
                trade_date=resolved,
                returned_members=len(members),
                member_basis=member_basis,
                member_observed_at=member_observed_at,
                units={
                    "daily.open/high/low/close/pre_close/avg_price/change": "index_point",
                    "daily.volume": "share",
                    "daily.pct_change/turnover_rate": "percent",
                    "daily.total_mv/float_mv": "CNY",
                    "members.weight": "percent",
                },
            )

        source_name = (source or "DC").upper()
        resolved = await self._resolve_date(
            ("market_theme_daily", "market_theme_member_daily"), trade_date
        )
        market_theme_detail_columns = (
            MarketThemeDaily.source,
            MarketThemeDaily.theme_code,
            MarketThemeDaily.trade_date,
            MarketThemeDaily.name,
            MarketThemeDaily.pct_change,
            MarketThemeDaily.hot,
            MarketThemeDaily.rank,
            MarketThemeDaily.strength,
            MarketThemeDaily.z_t_num,
            MarketThemeDaily.main_change,
            MarketThemeDaily.lead_stock,
            MarketThemeDaily.lead_stock_code,
            MarketThemeDaily.lead_stock_pct_change,
        )
        detail = await self._resolve_unique_topic(
            select(*market_theme_detail_columns)
            .where(
                MarketThemeDaily.source == source_name,
                MarketThemeDaily.trade_date == resolved,
                or_(
                    MarketThemeDaily.theme_code == identifier.upper(),
                    MarketThemeDaily.name.ilike(exact_pattern, escape="\\"),
                ),
            )
            .order_by(MarketThemeDaily.theme_code)
            .limit(6),
            select(*market_theme_detail_columns)
            .where(
                MarketThemeDaily.source == source_name,
                MarketThemeDaily.trade_date == resolved,
                MarketThemeDaily.name.ilike(pattern, escape="\\"),
            )
            .order_by(MarketThemeDaily.name, MarketThemeDaily.theme_code)
            .limit(6),
            topic_label="市场题材",
            identifier=identifier,
        )
        theme_code = str(detail["theme_code"])
        members = _mapping_rows(
            await self._query.execute(
                select(
                    MarketThemeMemberDaily.ts_code,
                    MarketThemeMemberDaily.name,
                    MarketThemeMemberDaily.industry_code,
                    MarketThemeMemberDaily.industry,
                    MarketThemeMemberDaily.reason,
                    MarketThemeMemberDaily.hot_num,
                )
                .where(
                    MarketThemeMemberDaily.source == source_name,
                    MarketThemeMemberDaily.trade_date == resolved,
                    MarketThemeMemberDaily.theme_code == theme_code,
                )
                .order_by(desc(MarketThemeMemberDaily.hot_num), MarketThemeMemberDaily.ts_code)
                .limit(member_limit)
            )
        )
        return _success(
            {"detail": dict(detail), "members": members},
            topic_type=topic_type,
            trade_date=resolved,
            returned_members=len(members),
            units={
                "detail.pct_change/detail.lead_stock_pct_change": "percent",
                "detail.main_change": "CNY",
                "detail.hot/detail.strength": "provider_score",
                "members.hot_num": "count",
            },
        )

    async def get_index_or_etf(
        self,
        asset_type: Literal["index", "etf"],
        code: str,
        *,
        action: Literal["snapshot", "history", "constituents"] = "snapshot",
        trade_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 250,
    ) -> JsonObject:
        if start_date and end_date and start_date > end_date:
            raise MarketQueryError("INVALID_REQUEST", "start_date cannot be later than end_date")
        code = code.upper()
        limit = min(max(limit, 1), 1000)
        if asset_type == "etf":
            if action == "constituents":
                raise MarketQueryError(
                    "INVALID_REQUEST", "ETF数据未提供成分股明细", asset_type=asset_type
                )
            profile = (
                (
                    await self._query.execute(
                        select(
                            Etf.ts_code,
                            Etf.csname,
                            Etf.extname,
                            Etf.cname,
                            Etf.index_code,
                            Etf.index_name,
                            Etf.setup_date,
                            Etf.list_date,
                            Etf.list_status,
                            Etf.exchange,
                            Etf.source_exchange,
                            Etf.mgr_name,
                            Etf.custod_name,
                            Etf.mgt_fee,
                            Etf.etf_type,
                        ).where(Etf.ts_code == code)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if profile is None:
                raise MarketQueryError("NOT_FOUND", "未找到ETF", asset_code=code)
            if action == "snapshot":
                datasets = ("etf_daily", "etf_share_size_daily")
                resolved = await self._resolve_date(datasets, trade_date)
                daily = (
                    (
                        await self._query.execute(
                            select(
                                EtfDaily.trade_date,
                                EtfDaily.open,
                                EtfDaily.high,
                                EtfDaily.low,
                                EtfDaily.close,
                                EtfDaily.pre_close,
                                EtfDaily.change,
                                EtfDaily.pct_chg,
                                EtfDaily.volume,
                                EtfDaily.amount,
                                EtfDaily.adj_factor,
                                EtfShareSizeDaily.etf_name,
                                EtfShareSizeDaily.total_share,
                                EtfShareSizeDaily.total_size,
                                EtfShareSizeDaily.nav,
                            )
                            .join(
                                EtfShareSizeDaily,
                                and_(
                                    EtfShareSizeDaily.ts_code == EtfDaily.ts_code,
                                    EtfShareSizeDaily.trade_date == EtfDaily.trade_date,
                                ),
                            )
                            .where(EtfDaily.ts_code == code, EtfDaily.trade_date == resolved)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if daily is None:
                    raise MarketQueryError(
                        "NOT_FOUND",
                        "指定ETF在已发布日期没有快照",
                        asset_code=code,
                        trade_date=resolved,
                    )
                return _success(
                    {"profile": dict(profile), "quote": dict(daily)},
                    asset_type=asset_type,
                    action=action,
                    trade_date=resolved,
                    datasets=datasets,
                    units={
                        "quote.open/high/low/close/pre_close/change/nav": "CNY",
                        "quote.amount/total_size": "CNY",
                        "quote.volume/total_share": "share",
                        "quote.pct_chg": "percent",
                        "quote.adj_factor": "dimensionless",
                        "profile.mgt_fee": "unknown_provider_unit",
                    },
                )
            statement = select(
                EtfDaily.trade_date,
                EtfDaily.open,
                EtfDaily.high,
                EtfDaily.low,
                EtfDaily.close,
                EtfDaily.pre_close,
                EtfDaily.change,
                EtfDaily.pct_chg,
                EtfDaily.volume,
                EtfDaily.amount,
                EtfDaily.adj_factor,
            ).where(
                EtfDaily.ts_code == code,
                EtfDaily.trade_date.in_(self._released_dates(("etf_daily",))),
            )
            if start_date:
                statement = statement.where(EtfDaily.trade_date >= start_date)
            if end_date:
                statement = statement.where(EtfDaily.trade_date <= end_date)
            statement = statement.order_by(desc(EtfDaily.trade_date)).limit(limit)
            rows = _mapping_rows(await self._query.execute(statement))
            return _success(
                {"profile": dict(profile), "history": rows},
                asset_type=asset_type,
                action=action,
                returned=len(rows),
                limit=limit,
                units={
                    "history.open/high/low/close/pre_close/change": "CNY",
                    "history.amount": "CNY",
                    "history.volume": "share",
                    "history.pct_chg": "percent",
                    "history.adj_factor": "dimensionless",
                    "profile.mgt_fee": "unknown_provider_unit",
                },
            )

        profile = (
            (
                await self._query.execute(
                    select(
                        MarketIndex.ts_code,
                        MarketIndex.name,
                        MarketIndex.fullname,
                        MarketIndex.market,
                        MarketIndex.publisher,
                        MarketIndex.index_type,
                        MarketIndex.category,
                        MarketIndex.base_date,
                        MarketIndex.base_point,
                        MarketIndex.list_date,
                        MarketIndex.weight_rule,
                        MarketIndex.description,
                        MarketIndex.exp_date,
                    ).where(MarketIndex.ts_code == code)
                )
            )
            .mappings()
            .one_or_none()
        )
        if profile is None:
            raise MarketQueryError("NOT_FOUND", "未找到指数", asset_code=code)
        if action == "snapshot":
            datasets = ("market_index_daily", "index_daily_basic")
            resolved = await self._resolve_date(datasets, trade_date)
            daily = (
                (
                    await self._query.execute(
                        select(
                            MarketIndexDaily.trade_date,
                            MarketIndexDaily.open,
                            MarketIndexDaily.high,
                            MarketIndexDaily.low,
                            MarketIndexDaily.close,
                            MarketIndexDaily.pre_close,
                            MarketIndexDaily.change,
                            MarketIndexDaily.pct_chg,
                            MarketIndexDaily.volume,
                            MarketIndexDaily.amount,
                            IndexDailyBasic.total_mv,
                            IndexDailyBasic.float_mv,
                            IndexDailyBasic.total_share,
                            IndexDailyBasic.float_share,
                            IndexDailyBasic.free_share,
                            IndexDailyBasic.turnover_rate,
                            IndexDailyBasic.turnover_rate_f,
                            IndexDailyBasic.pe,
                            IndexDailyBasic.pe_ttm,
                            IndexDailyBasic.pb,
                        )
                        .join(
                            IndexDailyBasic,
                            and_(
                                IndexDailyBasic.ts_code == MarketIndexDaily.ts_code,
                                IndexDailyBasic.trade_date == MarketIndexDaily.trade_date,
                            ),
                        )
                        .where(
                            MarketIndexDaily.ts_code == code,
                            MarketIndexDaily.trade_date == resolved,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if daily is None:
                raise MarketQueryError(
                    "NOT_FOUND",
                    "指定指数在已发布日期没有快照",
                    asset_code=code,
                    trade_date=resolved,
                )
            return _success(
                {"profile": dict(profile), "quote": dict(daily)},
                asset_type=asset_type,
                action=action,
                trade_date=resolved,
                datasets=datasets,
                units={
                    "quote.open/high/low/close/pre_close/change": "index_point",
                    "quote.volume": "share",
                    "quote.amount/total_mv/float_mv": "CNY",
                    "quote.total_share/float_share/free_share": "share",
                    "quote.pct_chg/turnover_rate/turnover_rate_f": "percent",
                    "quote.pe/pe_ttm/pb": "dimensionless",
                    "profile.base_point": "index_point",
                },
            )
        if action == "history":
            statement = select(
                MarketIndexDaily.trade_date,
                MarketIndexDaily.open,
                MarketIndexDaily.high,
                MarketIndexDaily.low,
                MarketIndexDaily.close,
                MarketIndexDaily.pre_close,
                MarketIndexDaily.change,
                MarketIndexDaily.pct_chg,
                MarketIndexDaily.volume,
                MarketIndexDaily.amount,
            ).where(
                MarketIndexDaily.ts_code == code,
                MarketIndexDaily.trade_date.in_(self._released_dates(("market_index_daily",))),
            )
            if start_date:
                statement = statement.where(MarketIndexDaily.trade_date >= start_date)
            if end_date:
                statement = statement.where(MarketIndexDaily.trade_date <= end_date)
            statement = statement.order_by(desc(MarketIndexDaily.trade_date)).limit(limit)
            rows = _mapping_rows(await self._query.execute(statement))
            return _success(
                {"profile": dict(profile), "history": rows},
                asset_type=asset_type,
                action=action,
                returned=len(rows),
                limit=limit,
                units={
                    "history.open/high/low/close/pre_close/change": "index_point",
                    "history.volume": "share",
                    "history.amount": "CNY",
                    "history.pct_chg": "percent",
                    "profile.base_point": "index_point",
                },
            )

        released_through = await self._latest_release_at_or_before(
            "market_index_weight", trade_date, scope_type="MONTH"
        )
        snapshot_statement = select(func.max(MarketIndexWeight.snapshot_date)).where(
            MarketIndexWeight.index_code == code,
            MarketIndexWeight.snapshot_date <= released_through,
        )
        snapshot_date = (await self._query.execute(snapshot_statement)).scalar_one_or_none()
        if snapshot_date is None:
            raise MarketQueryError(
                "NOT_FOUND",
                "未找到指数成分股快照",
                asset_code=code,
                trade_date=trade_date,
            )
        members = _mapping_rows(
            await self._query.execute(
                select(
                    MarketIndexWeight.con_code.label("ts_code"),
                    Stock.name,
                    Stock.industry,
                    Stock.exchange,
                    MarketIndexWeight.weight,
                )
                .join(Stock, Stock.ts_code == MarketIndexWeight.con_code)
                .where(
                    MarketIndexWeight.index_code == code,
                    MarketIndexWeight.snapshot_date == snapshot_date,
                )
                .order_by(desc(MarketIndexWeight.weight), MarketIndexWeight.con_code)
                .limit(limit)
            )
        )
        return _success(
            {"profile": dict(profile), "constituents": members},
            asset_type=asset_type,
            action=action,
            snapshot_date=snapshot_date,
            returned=len(members),
            limit=limit,
            units={
                "constituents.weight": "percent",
                "profile.base_point": "index_point",
            },
        )

    async def get_data_status(
        self,
        datasets: Sequence[str] | None = None,
    ) -> JsonObject:
        requested = tuple(dict.fromkeys(datasets or ()))
        if len(requested) > 50:
            raise MarketQueryError("INVALID_REQUEST", "datasets cannot contain more than 50 items")
        if requested:
            unknown = sorted(set(requested) - set(await self._known_datasets()))
            if unknown:
                raise MarketQueryError("UNKNOWN_DATASET", "存在未知数据集", datasets=unknown)
            names = requested
        else:
            names = await self._known_datasets()
        statuses: list[JsonObject] = []
        for dataset in names:
            row = (
                (
                    await self._query.execute(
                        select(
                            DatasetRelease.dataset_name,
                            DatasetRelease.scope_type,
                            DatasetRelease.scope_key,
                            DatasetRelease.business_date,
                            DatasetRelease.version_id,
                            DatasetRelease.process_id,
                            DatasetRelease.row_count,
                            DatasetRelease.published_at,
                        )
                        .where(DatasetRelease.dataset_name == dataset)
                        .order_by(
                            desc(DatasetRelease.business_date), desc(DatasetRelease.published_at)
                        )
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            statuses.append(
                dict(row)
                if row is not None
                else {"dataset_name": dataset, "status": "never_published"}
            )
        return _success(statuses, returned=len(statuses))

    async def _known_datasets(self) -> tuple[str, ...]:
        rows = (
            (
                await self._query.execute(
                    select(DatasetRelease.dataset_name)
                    .distinct()
                    .order_by(DatasetRelease.dataset_name)
                )
            )
            .scalars()
            .all()
        )
        return tuple(rows)
