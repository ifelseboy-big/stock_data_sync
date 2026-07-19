<!-- 来源：https://tcnq6fudd3wh.feishu.cn/docx/NJXndRs7eoRq7KxWPQ0cCyycnPc；飞书修订版：78 -->

# 4. A股基础、行情与资金表

## 4.1 trade_calendar

接口：[trade_cal](https://tushare.pro/document/2?doc_id=26)。本地交易日门禁，日常任务不应在每个接口前重复请求远端日历。

```sql
exchange varchar(8) not null -- 交易所代码，规范化为SSE或SZSE
cal_date date not null -- 自然日历日期
is_open boolean not null -- 当日是否为交易日
pretrade_date date null -- 当前日期之前最近一个交易日
synced_at timestamptz not null -- 本行最后同步时间
primary key (exchange, cal_date) -- 每个交易所每日一条记录
```

Tushare公开参数明确支持SSE和SZSE；A股日任务以本地SSE日历作为统一市场门禁，SZSE用于一致性核对。

## 4.2 stock

接口：[stock_basic](https://tushare.pro/document/2?doc_id=25)。必须分别拉取L/D/P/G，并按交易所拆分，不能依赖默认的L。

```sql
ts_code varchar(16) primary key -- Tushare统一股票代码
symbol varchar(8) not null -- 不含交易所后缀的证券代码
name varchar(64) not null -- 股票简称
area varchar(32) null -- 公司所在地域
industry varchar(64) null -- 股票所属行业
fullname varchar(160) null -- 公司中文全称
enname varchar(256) null -- 公司英文全称
cnspell varchar(32) null -- 股票简称的拼音缩写
market varchar(16) null -- 市场层级，如主板、创业板、科创板或北交所
exchange varchar(8) not null -- 交易所代码，规范化为SSE、SZSE或BSE
curr_type varchar(8) null -- 交易货币类型
list_status char(1) not null check (list_status in ('L','D','P','G')) -- 上市状态：上市、退市、暂停上市或过会未交易
list_date date null -- 上市日期
delist_date date null -- 退市日期
is_hs char(1) null check (is_hs in ('N','H','S')) -- 沪深港通标识：否、沪股通或深股通
act_name varchar(160) null -- 实际控制人名称
act_ent_type varchar(64) null -- 实际控制人企业性质
synced_at timestamptz not null -- 本行最后同步时间
```

## 4.3 stock_company

接口：[stock_company](https://tushare.pro/document/2?doc_id=112)。

```sql
ts_code varchar(16) primary key references stock(ts_code) -- 对应股票的Tushare统一代码
com_name varchar(160) null -- 公司全称
com_id varchar(32) null -- Tushare返回的公司编码
exchange varchar(8) null -- 公司股票所属交易所
chairman varchar(64) null -- 董事长姓名
manager varchar(64) null -- 总经理姓名
secretary varchar(64) null -- 董事会秘书姓名
reg_capital numeric(24,4) null -- 注册资本；源万元乘10000后统一为元
setup_date date null -- 公司成立日期
province varchar(32) null -- 注册所在省份
city varchar(32) null -- 注册所在城市
introduction text null -- 公司简介
website varchar(256) null -- 公司官方网站
email varchar(128) null -- 公司联系邮箱
office text null -- 公司办公地址
employees integer null -- 员工人数
main_business text null -- 主要业务及产品
business_scope text null -- 工商登记经营范围
synced_at timestamptz not null -- 本行最后同步时间
```

## 4.4 stock_daily

合并接口：[daily](https://tushare.pro/document/2?doc_id=27)、[adj_factor](https://tushare.pro/document/2?doc_id=28)、[daily_basic](https://tushare.pro/document/2?doc_id=32)；[stk_limit](https://tushare.pro/document/2?doc_id=183)只负责补充涨跌停价。粒度一致，所以合并为一张正式日表。

```sql
ts_code varchar(16) not null references stock(ts_code) -- 股票的Tushare统一代码
trade_date date not null -- 交易日期
open numeric(20,6) not null -- 当日开盘价
high numeric(20,6) not null -- 当日最高价
low numeric(20,6) not null -- 当日最低价
close numeric(20,6) not null -- 当日收盘价
pre_close numeric(20,6) not null -- 前一交易日收盘价
change numeric(20,6) not null -- 收盘价较前收盘价的涨跌额
pct_chg numeric(14,6) not null -- 当日涨跌幅，单位为百分比
volume numeric(24,4) not null -- 当日成交量；daily.vol由手乘100后统一为股
amount numeric(24,4) not null -- 当日成交额；daily.amount由千元乘1000后统一为元
after_hours_volume numeric(24,4) null -- 盘后成交量；ah_vol由手乘100后统一为股，2026-07-06起有值
after_hours_amount numeric(24,4) null -- 盘后成交额；ah_amount由千元乘1000后统一为元
adj_factor numeric(24,8) not null -- 当日复权因子
turnover_rate numeric(14,6) null -- 换手率，单位为百分比
turnover_rate_f numeric(14,6) null -- 基于自由流通股本的换手率，单位为百分比
volume_ratio numeric(14,6) null -- 量比
pe numeric(20,6) null -- 市盈率，总市值除以净利润；亏损时为空
pe_ttm numeric(20,6) null -- 滚动市盈率
pb numeric(20,6) null -- 市净率
ps numeric(20,6) null -- 市销率
ps_ttm numeric(20,6) null -- 滚动市销率
dv_ratio numeric(14,6) null -- 股息率，单位为百分比
dv_ttm numeric(14,6) null -- 滚动股息率，单位为百分比
total_share numeric(24,4) null -- 总股本；源万股乘10000后统一为股
float_share numeric(24,4) null -- 流通股本；源万股乘10000后统一为股
free_share numeric(24,4) null -- 自由流通股本；源万股乘10000后统一为股
total_mv numeric(24,4) null -- 总市值；源万元乘10000后统一为元
circ_mv numeric(24,4) null -- 流通市值；源万元乘10000后统一为元
limit_status smallint null check (limit_status between 0 and 6) -- 当日涨跌停状态，保留Tushare枚举值0至6
up_limit numeric(20,6) null -- 当日涨停价
down_limit numeric(20,6) null -- 当日跌停价
synced_at timestamptz not null -- 本行最后同步时间
primary key (ts_code, trade_date) -- 每只股票每个交易日一条记录
```

不保存limit_pre_close；stk_limit.pre_close只与本表pre_close核对。正式核心版本依赖daily、daily_basic和adj_factor；涨跌停价由独立加工任务补充并单独记录发布状态。停牌期间daily无记录，由stock_suspend_daily解释。

## 4.5 stock_technical_daily

接口：[stk_factor](https://tushare.pro/document/2?doc_id=296)。本表保存Tushare提供的技术因子和复权价格，不与本地动态计算结果混用。

```sql
ts_code varchar(16) not null references stock(ts_code) -- 股票的Tushare统一代码
trade_date date not null -- 指标所属交易日期
open_hfq numeric(20,6) null -- 后复权开盘价
open_qfq numeric(20,6) null -- Tushare历史当日快照口径的前复权开盘价
close_hfq numeric(20,6) null -- 后复权收盘价
close_qfq numeric(20,6) null -- Tushare历史当日快照口径的前复权收盘价
high_hfq numeric(20,6) null -- 后复权最高价
high_qfq numeric(20,6) null -- Tushare历史当日快照口径的前复权最高价
low_hfq numeric(20,6) null -- 后复权最低价
low_qfq numeric(20,6) null -- Tushare历史当日快照口径的前复权最低价
pre_close_hfq numeric(20,6) null -- 后复权前收盘价
pre_close_qfq numeric(20,6) null -- Tushare历史当日快照口径的前复权前收盘价
macd_dif numeric(20,8) null -- MACD指标的DIF快线值
macd_dea numeric(20,8) null -- MACD指标的DEA慢线值
macd numeric(20,8) null -- MACD柱值
kdj_k numeric(20,8) null -- KDJ指标K值
kdj_d numeric(20,8) null -- KDJ指标D值
kdj_j numeric(20,8) null -- KDJ指标J值
rsi_6 numeric(20,8) null -- 6周期相对强弱指标
rsi_12 numeric(20,8) null -- 12周期相对强弱指标
rsi_24 numeric(20,8) null -- 24周期相对强弱指标
boll_upper numeric(20,8) null -- 布林带上轨
boll_mid numeric(20,8) null -- 布林带中轨
boll_lower numeric(20,8) null -- 布林带下轨
cci numeric(20,8) null -- 顺势指标CCI值
synced_at timestamptz not null -- 本行最后同步时间
primary key (ts_code, trade_date) -- 每只股票每个交易日一条指标记录
```

接口同时返回的close、open、high、low、pre_close、change、pct_change、vol、amount和adj_factor已映射到stock_daily，只用于一致性核对，不在本表重复保存。官方明确说明历史前复权是“历史当日快照，不更新”。如以后增加本地动态前复权和本地指标，必须使用另一数据集名称及calc_version，不得覆盖本表语义。

## 4.6 stock_moneyflow_daily

接口：[moneyflow](https://tushare.pro/document/2?doc_id=170)。

```sql
ts_code varchar(16) not null references stock(ts_code) -- 股票的Tushare统一代码
trade_date date not null -- 交易日期
buy_sm_vol numeric(24,4) null -- 小单买入成交量；源手乘100后统一为股
sell_sm_vol numeric(24,4) null -- 小单卖出成交量；源手乘100后统一为股
buy_md_vol numeric(24,4) null -- 中单买入成交量；源手乘100后统一为股
sell_md_vol numeric(24,4) null -- 中单卖出成交量；源手乘100后统一为股
buy_lg_vol numeric(24,4) null -- 大单买入成交量；源手乘100后统一为股
sell_lg_vol numeric(24,4) null -- 大单卖出成交量；源手乘100后统一为股
buy_elg_vol numeric(24,4) null -- 特大单买入成交量；源手乘100后统一为股
sell_elg_vol numeric(24,4) null -- 特大单卖出成交量；源手乘100后统一为股
net_mf_vol numeric(24,4) null -- 净流入成交量；源手乘100后统一为股
buy_sm_amount numeric(24,4) null -- 小单买入金额；源万元乘10000后统一为元
sell_sm_amount numeric(24,4) null -- 小单卖出金额；源万元乘10000后统一为元
buy_md_amount numeric(24,4) null -- 中单买入金额；源万元乘10000后统一为元
sell_md_amount numeric(24,4) null -- 中单卖出金额；源万元乘10000后统一为元
buy_lg_amount numeric(24,4) null -- 大单买入金额；源万元乘10000后统一为元
sell_lg_amount numeric(24,4) null -- 大单卖出金额；源万元乘10000后统一为元
buy_elg_amount numeric(24,4) null -- 特大单买入金额；源万元乘10000后统一为元
sell_elg_amount numeric(24,4) null -- 特大单卖出金额；源万元乘10000后统一为元
net_mf_amount numeric(24,4) null -- 净流入金额；源万元乘10000后统一为元
synced_at timestamptz not null -- 本行最后同步时间
primary key (ts_code, trade_date) -- 每只股票每个交易日一条资金流记录
```

所有vol源字段由手乘100转股；所有amount源字段由万元乘10000转元。net_mf不能由其他列自行相减替代。

## 4.7 ths_board_moneyflow_daily

合并接口：[moneyflow_cnt_ths](https://tushare.pro/document/2?doc_id=371)和[moneyflow_ind_ths](https://tushare.pro/document/2?doc_id=343)。

```sql
board_type varchar(16) not null check (board_type in ('CONCEPT','INDUSTRY')) -- 板块类型：概念或行业
ts_code varchar(20) not null -- 同花顺板块代码
trade_date date not null -- 交易日期
board_name varchar(128) not null -- 板块名称
lead_stock varchar(64) null -- 领涨股票名称
lead_stock_price numeric(20,6) null -- 领涨股票价格
pct_change numeric(14,6) null -- 板块涨跌幅，单位为百分比
board_index numeric(20,6) null -- 板块指数点位
company_num integer null -- 板块成分股票数量
lead_stock_pct_change numeric(14,6) null -- 领涨股票涨跌幅，单位为百分比
net_buy_amount numeric(24,4) null -- 板块流入金额；源亿元乘100000000后统一为元
net_sell_amount numeric(24,4) null -- 板块流出金额；源亿元乘100000000后统一为元
net_amount numeric(24,4) null -- 板块净流入金额；源亿元乘100000000后统一为元
synced_at timestamptz not null -- 本行最后同步时间
primary key (board_type, ts_code, trade_date) -- 每类板块每日每个代码一条记录
```

三个资金字段源单位亿元，统一乘100000000转元。CONCEPT记录可与concept_board核对；行业记录暂不强制关联概念主表。

## 4.8 stock_suspend_daily

接口：[suspend_d](https://tushare.pro/document/2?doc_id=214)。

```sql
ts_code varchar(16) not null references stock(ts_code) -- 股票的Tushare统一代码
trade_date date not null -- 停牌或复牌发生日期
suspend_type char(1) not null check (suspend_type in ('S','R')) -- 事件类型：S为停牌，R为复牌
suspend_timing varchar(64) null -- 停复牌具体时点或时间区间，保留源字符串
synced_at timestamptz not null -- 本行最后同步时间
primary key (ts_code, trade_date, suspend_type) -- 每只股票每日每类停复牌事件一条记录
```

suspend_timing保留源字符串，因为可能是“09:30-10:00”这样的区间，不设计成单一time字段。

