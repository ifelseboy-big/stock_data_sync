# 指数与 ETF 表

## 指数表

### market_index

接口：[index_basic](https://tushare.pro/document/2?doc_id=94)。必须按SSE、SZSE、CSI、MSCI、CICC、SW、OTH等明确支持范围分别调用；接口默认只返回SSE。

```sql
ts_code varchar(20) primary key -- Tushare统一指数代码
name varchar(128) not null -- 指数简称
fullname varchar(256) null -- 指数全称
market varchar(16) not null -- 指数所属市场或指数体系，如SSE、SZSE、CSI等
publisher varchar(128) null -- 指数发布机构
index_type varchar(64) null -- 指数风格或类型
category varchar(64) null -- 指数类别
base_date date null -- 指数基期日期
base_point numeric(20,6) null -- 指数基点
list_date date null -- 指数发布日期
weight_rule varchar(128) null -- 指数加权方式
description text null -- 指数描述，对应源字段desc
exp_date date null -- 指数终止日期
synced_at timestamptz not null -- 本行最后同步时间
```

market_index可以包含多类指数，但本项目只为配置中的A股相关指数调度行情。不能假定主表中的每一个指数都能通过index_daily获得日线。

### market_index_daily

接口：[index_daily](https://tushare.pro/document/2?doc_id=95)。

```sql
ts_code varchar(20) not null references market_index(ts_code) -- 指数的Tushare统一代码
trade_date date not null -- 交易日期
close numeric(20,6) not null -- 指数收盘点位
open numeric(20,6) null -- 指数开盘点位
high numeric(20,6) null -- 指数最高点位
low numeric(20,6) null -- 指数最低点位
pre_close numeric(20,6) null -- 指数前收盘点位
change numeric(20,6) null -- 指数较前收盘的涨跌额
pct_chg numeric(14,6) null -- 指数涨跌幅，单位为百分比
volume numeric(24,4) null -- 指数成交量；源vol由手乘100后统一为股
amount numeric(24,4) null -- 指数成交额；源amount由千元乘1000后统一为元
synced_at timestamptz not null -- 本行最后同步时间
primary key (ts_code, trade_date) -- 每个指数每个交易日一条行情
```

index_daily要求ts_code，必须从已配置的market_index代码清单逐个或分批调用；该接口明确不覆盖申万指数。

### index_daily_basic

接口：[index_dailybasic](https://tushare.pro/document/2?doc_id=128)。

```sql
ts_code varchar(20) not null references market_index(ts_code) -- 指数的Tushare统一代码
trade_date date not null -- 指标所属交易日期
total_mv numeric(24,4) null -- 指数成分总市值；源接口单位已为元
float_mv numeric(24,4) null -- 指数成分流通市值；源接口单位已为元
total_share numeric(24,4) null -- 指数成分总股本；源接口单位已为股
float_share numeric(24,4) null -- 指数成分流通股本；源接口单位已为股
free_share numeric(24,4) null -- 指数成分自由流通股本；源接口单位已为股
turnover_rate numeric(14,6) null -- 指数换手率，单位为百分比
turnover_rate_f numeric(14,6) null -- 基于自由流通股本的指数换手率，单位为百分比
pe numeric(20,6) null -- 指数静态市盈率
pe_ttm numeric(20,6) null -- 指数滚动市盈率
pb numeric(20,6) null -- 指数市净率
synced_at timestamptz not null -- 本行最后同步时间
primary key (ts_code, trade_date) -- 每个指数每个交易日一条估值指标记录
```

源接口的市值已为元、股本已为股，不重复换算。该接口只覆盖官方列出的少数大盘指数，不能代替market_index_daily。

### market_index_weight

接口：[index_weight](https://tushare.pro/document/2?doc_id=96)。本项目只保存A股指数且con_code能够关联stock的月度快照。

```sql
index_code varchar(20) not null references market_index(ts_code) -- 指数的Tushare统一代码
snapshot_date date not null -- 指数成分权重公开快照日期，对应源trade_date
con_code varchar(16) not null references stock(ts_code) -- 指数成分股票的Tushare统一代码
weight numeric(14,8) not null -- 成分股票在指数中的权重，单位为百分比
synced_at timestamptz not null -- 本行最后同步时间
primary key (index_code, snapshot_date, con_code) -- 每个指数每个快照日每只成分股票一条记录
```

源trade_date映射为snapshot_date，只表示该月公开快照，不解释为每日生效日，也不用于日频精确归因。

## ETF 表

### etf

接口：[etf_basic](https://tushare.pro/document/2?doc_id=385)。必须分别获取L/D/P状态。

```sql
ts_code varchar(16) primary key -- ETF基金交易代码
csname varchar(64) null -- ETF中文简称
extname varchar(96) null -- ETF扩位简称，即交易所简称
cname varchar(192) null -- ETF基金中文全称
index_code varchar(20) null -- ETF基准或跟踪指数代码
index_name varchar(192) null -- ETF基准或跟踪指数中文全称
setup_date date null -- ETF设立日期
list_date date null -- ETF上市日期
list_status char(1) not null check (list_status in ('L','D','P')) -- ETF存续状态：上市、退市或待上市
exchange varchar(8) not null -- 规范化交易所代码：SSE、SZSE或BSE
source_exchange varchar(8) not null -- Tushare源交易所代码：SH或SZ
mgr_name varchar(128) null -- 基金管理人简称
custod_name varchar(160) null -- 基金托管人名称
mgt_fee numeric(14,8) null -- 基金管理人收取的费用；官方未明确单位，保留原值
etf_type varchar(32) null -- 基金投资通道类型，如境内或QDII
synced_at timestamptz not null -- 本行最后同步时间
```

mgt_fee保留Tushare原值；官方未明确其单位，未抽样确认前不得把它直接解释为百分比或小数费率。index_code不强制外键到market_index，因为部分跟踪指数可能不在本项目指数支持集内。

### etf_daily

合并接口：[fund_daily](https://tushare.pro/document/2?doc_id=127)和[fund_adj](https://tushare.pro/document/2?doc_id=199)。

```sql
ts_code varchar(16) not null references etf(ts_code) -- ETF基金交易代码
trade_date date not null -- 交易日期
open numeric(20,6) not null -- 当日开盘价，单位为元
high numeric(20,6) not null -- 当日最高价，单位为元
low numeric(20,6) not null -- 当日最低价，单位为元
close numeric(20,6) not null -- 当日收盘价，单位为元
pre_close numeric(20,6) not null -- 前一交易日收盘价，单位为元
change numeric(20,6) not null -- 收盘价较前收盘价的涨跌额，单位为元
pct_chg numeric(14,6) not null -- 当日涨跌幅，单位为百分比
volume numeric(24,4) not null -- 当日成交量；源vol由手乘100后统一为份
amount numeric(24,4) not null -- 当日成交额；源amount由千元乘1000后统一为元
adj_factor numeric(24,8) null -- ETF复权因子
synced_at timestamptz not null -- 本行最后同步时间
primary key (ts_code, trade_date) -- 每只ETF每个交易日一条行情
```

fund_adj包含其他公募基金数据，正式写入前必须与etf主表内连接，只保留ETF代码。ETF与股票保持分表。

### etf_share_size_daily

接口：[etf_share_size](https://tushare.pro/document/2?doc_id=408)。

```sql
ts_code varchar(16) not null references etf(ts_code) -- ETF基金交易代码
trade_date date not null -- 份额规模数据所属交易日期
etf_name varchar(96) null -- ETF基金名称
total_share numeric(24,4) not null -- ETF总份额；源万份乘10000后统一为份
total_size numeric(24,4) not null -- ETF总规模；源万元乘10000后统一为元
nav numeric(20,8) null -- 基金份额净值，单位为元
close numeric(20,6) null -- ETF当日收盘价，单位为元
exchange varchar(8) not null -- 规范化交易所代码：SSE、SZSE或BSE
synced_at timestamptz not null -- 本行最后同步时间
primary key (ts_code, trade_date) -- 每只ETF每个交易日一条份额规模记录
```

该数据次日约08:30分批更新，海外ETF更晚。本表独立发布，不并入etf_daily，因此份额延迟不会阻塞ETF日线行情。
