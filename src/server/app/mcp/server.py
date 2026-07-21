from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from app.mcp.database import (
    dispose_mcp_read_only_engine,
    initialize_mcp_read_only_database,
    mcp_read_only_query,
)
from app.modules.market_query.schemas import (
    ScreenFilter,
    ScreenSort,
    ScreenUniverse,
    SecurityType,
)
from app.modules.market_query.service import (
    JsonObject,
    MarketQueryError,
    MarketQueryService,
    failure,
)

READ_ONLY_TOOL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@asynccontextmanager
async def app_lifespan(_server: FastMCP[Any]) -> AsyncIterator[None]:
    await initialize_mcp_read_only_database()
    try:
        yield None
    finally:
        await dispose_mcp_read_only_engine()


mcp = FastMCP(
    name="stock-data-readonly",
    instructions=(
        "本机股票数据只读查询服务。只能调用固定工具；不接受任意SQL，不提供写入、同步或运维能力。"
        "未指定交易日时，工具基于dataset_release选择相关数据集最新共同发布日期。"
    ),
    lifespan=app_lifespan,
    log_level="WARNING",
)


async def _run(
    operation: Callable[[MarketQueryService], Awaitable[JsonObject]],
) -> JsonObject:
    async with mcp_read_only_query() as query:
        service = MarketQueryService(query)
        try:
            return await operation(service)
        except MarketQueryError as exc:
            return failure(exc)


@mcp.tool(
    description="按代码或名称搜索股票、ETF和指数。",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
async def search_securities(
    keyword: str,
    security_types: list[SecurityType] | None = None,
    limit: int = 20,
) -> JsonObject:
    return await _run(
        lambda service: service.search_securities(keyword, security_types, limit=limit)
    )


@mcp.tool(
    description="查询股票在已完整发布交易日的行情、估值、技术指标和资金流快照。",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
async def get_stock_snapshot(
    ts_code: str,
    trade_date: date | None = None,
) -> JsonObject:
    return await _run(lambda service: service.get_stock_snapshot(ts_code, trade_date))


@mcp.tool(
    description="查询股票历史行情，可选前复权、后复权和资金流。最多1000条。",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
async def get_stock_history(
    ts_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
    adjustment: Literal["raw", "qfq", "hfq"] = "raw",
    include_moneyflow: bool = False,
    order: Literal["asc", "desc"] = "desc",
    limit: int = 250,
) -> JsonObject:
    return await _run(
        lambda service: service.get_stock_history(
            ts_code,
            start_date=start_date,
            end_date=end_date,
            adjustment=adjustment,
            include_moneyflow=include_moneyflow,
            order=order,
            limit=limit,
        )
    )


@mcp.tool(
    description="返回选股可用字段、类型和操作符。",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
async def list_screen_fields() -> JsonObject:
    async with mcp_read_only_query() as query:
        return MarketQueryService(query).list_screen_fields()


@mcp.tool(
    description="按白名单字段组合筛选股票；支持all/any条件、股票池和排序。最多200条。",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
async def screen_stocks(
    filters: Annotated[list[ScreenFilter] | None, Field(max_length=20)] = None,
    match: Literal["all", "any"] = "all",
    universe: ScreenUniverse | None = None,
    fields: Annotated[list[str] | None, Field(max_length=50)] = None,
    sort: Annotated[list[ScreenSort] | None, Field(max_length=5)] = None,
    trade_date: date | None = None,
    limit: int = 50,
) -> JsonObject:
    return await _run(
        lambda service: service.screen_stocks(
            filters=filters or (),
            match=match,
            universe=universe,
            fields=fields,
            sort=sort or (),
            trade_date=trade_date,
            limit=limit,
        )
    )


@mcp.tool(
    description="查询股票热榜、龙虎榜、机构、涨跌停、停复牌和题材事件。",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
async def get_stock_events(
    ts_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
    limit_per_type: int = 50,
) -> JsonObject:
    return await _run(
        lambda service: service.get_stock_events(
            ts_code,
            start_date=start_date,
            end_date=end_date,
            limit_per_type=limit_per_type,
        )
    )


@mcp.tool(
    description="查询热榜、涨跌停梯队、龙虎榜、板块资金流或市场题材排行。",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
async def get_market_rankings(
    kind: Literal["hot", "limit", "limit_step", "top_list", "board_moneyflow", "market_theme"],
    trade_date: date | None = None,
    source: str | None = None,
    rank_type: str | None = None,
    limit_type: Literal["U", "D", "Z"] | None = None,
    board_type: Literal["CONCEPT", "INDUSTRY"] | None = None,
    limit: int = 50,
) -> JsonObject:
    return await _run(
        lambda service: service.get_market_rankings(
            kind,
            trade_date=trade_date,
            source=source,
            rank_type=rank_type,
            limit_type=limit_type,
            board_type=board_type,
            limit=limit,
        )
    )


@mcp.tool(
    description="查询概念板块、主题指数或每日市场题材及其成分。",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
async def get_topic(
    topic_type: Literal["concept", "theme_index", "market_theme"],
    identifier: str,
    trade_date: date | None = None,
    source: str | None = None,
    member_limit: int = 200,
) -> JsonObject:
    return await _run(
        lambda service: service.get_topic(
            topic_type,
            identifier,
            trade_date=trade_date,
            source=source,
            member_limit=member_limit,
        )
    )


@mcp.tool(
    description="查询指数或ETF快照、历史；指数还可查询成分权重。",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
async def get_index_or_etf(
    asset_type: Literal["index", "etf"],
    code: str,
    action: Literal["snapshot", "history", "constituents"] = "snapshot",
    trade_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 250,
) -> JsonObject:
    return await _run(
        lambda service: service.get_index_or_etf(
            asset_type,
            code,
            action=action,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
    )


@mcp.tool(
    description="查询数据集最近一次发布状态，用于判断数据日期和完整性。",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
async def get_data_status(
    datasets: Annotated[list[str] | None, Field(max_length=50)] = None,
) -> JsonObject:
    return await _run(lambda service: service.get_data_status(datasets))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
