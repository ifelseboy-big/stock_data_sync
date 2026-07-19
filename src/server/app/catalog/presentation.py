from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApiPresentation:
    api_name: str
    display_name: str
    description: str


@dataclass(frozen=True, slots=True)
class DatasetPresentation:
    dataset_name: str
    display_name: str
    description: str


TUSHARE_API_PRESENTATIONS = (
    ApiPresentation("trade_cal", "交易日历", "同步交易所开休市日期，供每日任务判断交易日。"),
    ApiPresentation("stock_basic", "股票列表", "同步股票代码、名称、市场、上市状态和退市日期。"),
    ApiPresentation(
        "stock_company", "上市公司资料", "同步公司名称、管理层、地区和主营业务等资料。"
    ),
    ApiPresentation("etf_basic", "ETF 列表", "同步 ETF 代码、名称、跟踪指数、管理人和上市状态。"),
    ApiPresentation("ths_index", "同花顺概念与主题列表", "同步同花顺概念板块和主题指数基础信息。"),
    ApiPresentation("index_basic", "市场指数列表", "同步上证、深证、创业板等指数基础信息。"),
    ApiPresentation(
        "ths_member", "同花顺概念与主题成分", "同步每个同花顺概念和主题指数包含的股票。"
    ),
    ApiPresentation("adj_factor", "股票复权因子", "同步股票复权因子，用于计算前复权和后复权行情。"),
    ApiPresentation("daily", "股票日线行情", "同步股票开高低收、成交量和成交额。"),
    ApiPresentation("fund_daily", "ETF 日线行情", "同步 ETF 开高低收、成交量和成交额。"),
    ApiPresentation(
        "daily_basic", "股票每日估值指标", "同步换手率、PE、PB、市值和股本等每日指标。"
    ),
    ApiPresentation("stk_limit", "股票每日涨跌停价格", "同步股票当日涨停价和跌停价。"),
    ApiPresentation("moneyflow", "股票每日资金流向", "同步股票大中小单买卖金额及净流入。"),
    ApiPresentation("suspend_d", "股票停复牌信息", "同步股票当日停牌、复牌时间和原因。"),
    ApiPresentation("fund_adj", "ETF 复权因子", "同步 ETF 复权因子并合并到 ETF 日线。"),
    ApiPresentation("ths_daily", "同花顺概念与主题日线", "同步同花顺概念板块和主题指数每日行情。"),
    ApiPresentation("dc_concept", "东方财富题材列表", "同步东方财富每日动态题材、热度和领涨股票。"),
    ApiPresentation("top_list", "龙虎榜股票明细", "同步每日龙虎榜上榜股票和买卖金额。"),
    ApiPresentation("top_inst", "龙虎榜席位明细", "同步龙虎榜营业部和机构席位买卖明细。"),
    ApiPresentation("limit_list_d", "涨跌停股票明细", "同步涨停、跌停、炸板及开板次数等信息。"),
    ApiPresentation("limit_step", "连板阶梯", "同步每日连续涨停股票及连板数量。"),
    ApiPresentation("moneyflow_cnt_ths", "同花顺概念资金流", "同步同花顺概念板块每日资金流向。"),
    ApiPresentation("moneyflow_ind_ths", "同花顺行业资金流", "同步同花顺行业板块每日资金流向。"),
    ApiPresentation("index_daily", "指数日线行情", "同步主要市场指数每日行情。"),
    ApiPresentation(
        "index_dailybasic", "指数每日估值指标", "同步指数 PE、PB、换手率和市值等指标。"
    ),
    ApiPresentation("stk_factor", "股票技术指标", "同步复权行情、MACD、KDJ、RSI、BOLL 和 CCI。"),
    ApiPresentation("etf_share_size", "ETF 份额与规模", "同步 ETF 最新份额、基金规模和净值。"),
    ApiPresentation(
        "dc_concept_cons", "东方财富题材成分", "同步每日动态题材包含的股票及入选原因。"
    ),
    ApiPresentation("ths_hot", "同花顺股票热榜", "同步同花顺股票热度排名。"),
    ApiPresentation("dc_hot", "东方财富股票热榜", "同步东方财富股票人气排名。"),
    ApiPresentation("index_weight", "指数成分权重", "同步主要指数的月度成分股及权重。"),
)

TUSHARE_API_PRESENTATION_BY_NAME = {item.api_name: item for item in TUSHARE_API_PRESENTATIONS}


DATASET_PRESENTATIONS = (
    DatasetPresentation("trade_calendar", "交易日历", "沪深交易所开休市日期。"),
    DatasetPresentation("stock", "股票主数据", "股票代码、名称、市场及上市状态。"),
    DatasetPresentation("stock_company", "上市公司资料", "上市公司地区、管理层和主营业务资料。"),
    DatasetPresentation("etf", "ETF 主数据", "ETF 代码、名称、跟踪指数和上市状态。"),
    DatasetPresentation("etf_daily", "ETF 日线行情", "ETF 每日价格、成交量和成交额。"),
    DatasetPresentation("etf_share_size_daily", "ETF 份额与规模", "ETF 每日份额、基金规模和净值。"),
    DatasetPresentation("stock_daily.core", "股票核心日线", "股票每日行情、复权因子和估值指标。"),
    DatasetPresentation("stock_daily.limit", "股票涨跌停价格", "股票每日涨停价和跌停价。"),
    DatasetPresentation(
        "stock_technical_daily", "股票技术指标", "股票 MACD、KDJ、RSI、BOLL 和 CCI。"
    ),
    DatasetPresentation("stock_moneyflow_daily", "股票资金流", "股票大中小单买卖和净流入。"),
    DatasetPresentation("stock_suspend_daily", "股票停复牌", "股票停牌、复牌时间和原因。"),
    DatasetPresentation("concept_board", "同花顺概念板块", "同花顺概念板块基础信息。"),
    DatasetPresentation("concept_board_daily", "同花顺概念板块日线", "同花顺概念板块每日行情。"),
    DatasetPresentation("concept_board_member", "同花顺概念板块成分", "同花顺概念板块包含的股票。"),
    DatasetPresentation("theme_index", "同花顺主题指数", "同花顺主题指数基础信息。"),
    DatasetPresentation("theme_index_daily", "同花顺主题指数日线", "同花顺主题指数每日行情。"),
    DatasetPresentation("theme_index_member", "同花顺主题指数成分", "同花顺主题指数包含的股票。"),
    DatasetPresentation("stock_hot_rank_daily", "股票热度排名", "同花顺和东方财富股票热度排名。"),
    DatasetPresentation("market_theme_daily", "东方财富动态题材", "东方财富每日动态题材及热度。"),
    DatasetPresentation(
        "market_theme_member_daily", "东方财富题材成分", "东方财富每日题材包含的股票。"
    ),
    DatasetPresentation("stock_top_list_daily", "龙虎榜股票明细", "每日龙虎榜上榜股票和买卖金额。"),
    DatasetPresentation(
        "stock_top_inst_daily", "龙虎榜席位明细", "龙虎榜营业部和机构席位买卖明细。"
    ),
    DatasetPresentation("stock_limit_event_daily", "涨跌停股票明细", "每日涨停、跌停和炸板股票。"),
    DatasetPresentation("stock_limit_step_daily", "连板阶梯", "每日连续涨停股票及连板数量。"),
    DatasetPresentation(
        "ths_board_moneyflow_daily", "同花顺板块资金流", "同花顺概念和行业板块资金流。"
    ),
    DatasetPresentation("market_index", "市场指数", "上证、深证和创业板等指数基础信息。"),
    DatasetPresentation("market_index_daily", "市场指数日线", "主要市场指数每日行情。"),
    DatasetPresentation("index_daily_basic", "指数估值指标", "指数 PE、PB、换手率和市值。"),
    DatasetPresentation("market_index_weight", "指数成分权重", "主要指数的成分股及权重。"),
)

DATASET_PRESENTATION_BY_NAME = {item.dataset_name: item for item in DATASET_PRESENTATIONS}
