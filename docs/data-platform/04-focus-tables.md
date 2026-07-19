<!-- 来源：https://tcnq6fudd3wh.feishu.cn/docx/NJXndRs7eoRq7KxWPQ0cCyycnPc；飞书修订版：78 -->

# 5. 概念、热榜、题材与龙虎榜表

## 5.1 concept_board

接口：[ths_index](https://tushare.pro/document/2?doc_id=259)。当前只采集exchange=A、type=N的A股概念。

```sql
source varchar(8) not null default 'THS' -- 板块数据来源，当前固定为同花顺THS
ts_code varchar(20) not null -- 同花顺概念板块代码
name varchar(128) not null -- 概念板块名称
member_count integer null -- 板块成分数量，对应源字段count
exchange varchar(8) null -- 市场范围；当前采集A股范围A
list_date date null -- 板块发布日期
board_type varchar(8) not null -- 板块类型，对应源字段type；当前采集概念类型N
synced_at timestamptz not null -- 本行最后同步时间
primary key (source, ts_code) -- 每个来源内板块代码唯一
```

## 5.2 concept_board_daily

接口：[ths_daily](https://tushare.pro/document/2?doc_id=260)。

```sql
source varchar(8) not null default 'THS' -- 板块数据来源，当前固定为同花顺THS
ts_code varchar(20) not null -- 同花顺概念板块代码
trade_date date not null -- 交易日期
close numeric(20,6) not null -- 板块收盘点位
open numeric(20,6) null -- 板块开盘点位
high numeric(20,6) null -- 板块最高点位
low numeric(20,6) null -- 板块最低点位
pre_close numeric(20,6) null -- 板块前收盘点位
avg_price numeric(20,6) null -- 板块当日平均价
change numeric(20,6) null -- 板块较前收盘的涨跌额
pct_change numeric(14,6) null -- 板块涨跌幅，单位为百分比
volume numeric(24,4) null -- 板块成交量；源vol由手乘100后统一为股
turnover_rate numeric(14,6) null -- 板块换手率，单位为百分比
total_mv numeric(24,4) null -- 板块总市值；源接口单位已为元
float_mv numeric(24,4) null -- 板块流通市值；源接口单位已为元
synced_at timestamptz not null -- 本行最后同步时间
primary key (source, ts_code, trade_date) -- 每个来源的每个板块每日一条行情
foreign key (source, ts_code) references concept_board(source, ts_code) -- 关联概念板块主表
```

## 5.3 concept_board_member

接口：[ths_member](https://tushare.pro/document/2?doc_id=261)。本表只表达“最近一次完整采集时的当前成员”，不宣称真实历史有效期。

```sql
source varchar(8) not null default 'THS' -- 板块数据来源，当前固定为同花顺THS
ts_code varchar(20) not null -- 同花顺概念板块代码
con_code varchar(16) not null references stock(ts_code) -- 板块成分股票的Tushare统一代码
con_name varchar(64) null -- 成分股票名称
weight numeric(14,8) null -- 成分权重；官方当前标为暂无，保持为空
in_date date null -- 纳入日期；官方当前标为暂无，保持为空
out_date date null -- 剔除日期；官方当前标为暂无，保持为空
is_current boolean not null -- 是否属于最近一次完整快照的当前成分
observed_at date not null -- 本项目观察到该成员关系的快照日期，不代表纳入日期
synced_at timestamptz not null -- 本行最后同步时间
primary key (source, ts_code, con_code) -- 每个来源的板块和成分股票关系唯一
foreign key (source, ts_code) references concept_board(source, ts_code) -- 关联概念板块主表
```

同步只发布is_new=Y的完整快照，并按板块原子替换。官方当前将weight、in_date、out_date标为“暂无”；NULL必须保持NULL，observed_at只是观察日期，不能冒充纳入日期。

## 5.4 stock_hot_rank_daily

接口：[ths_hot](https://tushare.pro/document/2?doc_id=320)和[dc_hot](https://tushare.pro/document/2?doc_id=321)。只保存A股股票最终榜，不混入ETF、概念板块、港美股。

```sql
source varchar(8) not null check (source in ('THS','DC')) -- 热榜来源：同花顺THS或东方财富DC
trade_date date not null -- 榜单所属交易日期
market_type varchar(32) not null -- 请求时指定的市场类型；本表只发布A股股票榜
rank_type varchar(32) not null -- 榜单类型；THS固定FINAL，DC区分人气榜或飙升榜
data_type varchar(32) null -- 源记录的数据类型
ts_code varchar(16) not null references stock(ts_code) -- 上榜股票的Tushare统一代码
ts_name varchar(64) null -- 上榜股票名称
rank integer not null check (rank > 0) -- 榜单名次，从1开始
pct_change numeric(14,6) null -- 股票涨跌幅，单位为百分比
current_price numeric(20,6) null -- 榜单采集时的当前价格
concept jsonb null -- 源接口返回的概念或标签列表
rank_reason text null -- 上榜原因或上榜解读
hot numeric(24,6) null -- 热度值
rank_time timestamptz not null -- 榜单获取时间，按Asia/Shanghai解析
synced_at timestamptz not null -- 本行最后同步时间
primary key (source, trade_date, market_type, rank_type, ts_code) -- 同一榜单内每只股票一条记录
unique (source, trade_date, market_type, rank_type, rank) -- 同一榜单内名次唯一
```

market_type和rank_type来自请求上下文。THS没有hot_type，固定rank_type='FINAL'；DC保存“人气榜/飙升榜”。rank_time按Asia/Shanghai解析，且只有is_new=Y、22:30最终批次可以发布。

## 5.5 market_theme_daily

接口：[dc_concept](https://tushare.pro/document/2?doc_id=421)。

```sql
source varchar(8) not null default 'DC' -- 题材数据来源，当前固定为东方财富DC
theme_code varchar(20) not null -- 东方财富题材代码
trade_date date not null -- 题材数据所属交易日期
name varchar(128) not null -- 题材名称
pct_change numeric(14,6) null -- 题材涨跌幅，单位为百分比
hot numeric(24,6) null -- 题材热度值
rank integer null -- 题材排名，对应源字段sort
strength numeric(24,6) null -- 题材强度值
z_t_num integer null -- 题材内涨停股票数量
main_change numeric(24,4) null -- 题材主力净流入金额；源接口单位已为元
lead_stock varchar(64) null -- 领涨股票名称
lead_stock_code varchar(16) null -- 领涨股票的Tushare统一代码
lead_stock_pct_change numeric(14,6) null -- 领涨股票涨跌幅，单位为百分比
synced_at timestamptz not null -- 本行最后同步时间
primary key (source, theme_code, trade_date) -- 每个来源的每个题材每日一条记录
```

## 5.6 market_theme_member_daily

接口：[dc_concept_cons](https://tushare.pro/document/2?doc_id=422)。

```sql
source varchar(8) not null default 'DC' -- 题材数据来源，当前固定为东方财富DC
trade_date date not null -- 成分关系所属交易日期
theme_code varchar(20) not null -- 东方财富题材代码
ts_code varchar(16) not null references stock(ts_code) -- 题材成分股票的Tushare统一代码
name varchar(64) null -- 成分股票名称
industry_code varchar(20) null -- 成分股票所属行业代码
industry varchar(64) null -- 成分股票所属行业名称
reason text null -- 股票入选该题材的原因
hot_num integer null -- 股票热点排行
synced_at timestamptz not null -- 本行最后同步时间
primary key (source, trade_date, theme_code, ts_code) -- 每个来源的题材每日每只成分股票一条记录
foreign key (source, theme_code, trade_date) -- 题材成分表的父记录外键
  references market_theme_daily(source, theme_code, trade_date) -- 关联同日题材主记录
```

接口单次最多3000行，必须按theme_code拆分获取并核对每个题材的成员数，不能只按交易日调用一次。

## 5.7 stock_top_list_daily

接口：[top_list](https://tushare.pro/document/2?doc_id=106)。

```sql
top_list_id bigint generated always as identity primary key -- 本地生成的龙虎榜统计记录唯一标识
trade_date date not null -- 上榜交易日期
ts_code varchar(16) not null references stock(ts_code) -- 上榜股票的Tushare统一代码
name varchar(64) null -- 股票名称
close numeric(20,6) null -- 当日收盘价
pct_change numeric(14,6) null -- 当日涨跌幅，单位为百分比
turnover_rate numeric(14,6) null -- 当日换手率，单位为百分比
amount numeric(24,4) null -- 股票当日总成交额，保留接口金额口径
l_sell numeric(24,4) null -- 龙虎榜卖出额，保留接口金额口径
l_buy numeric(24,4) null -- 龙虎榜买入额，保留接口金额口径
l_amount numeric(24,4) null -- 龙虎榜成交额，保留接口金额口径
net_amount numeric(24,4) null -- 龙虎榜净买入额，保留接口金额口径
net_rate numeric(14,6) null -- 龙虎榜净买入额占比，单位为百分比
amount_rate numeric(14,6) null -- 龙虎榜成交额占股票总成交额比例，单位为百分比
float_values numeric(24,4) null -- 股票当日流通市值，保留接口金额口径
reason varchar(512) not null -- 上榜理由
synced_at timestamptz not null -- 本行最后同步时间
unique (trade_date, ts_code, reason) -- 同股同日同一上榜理由唯一
```

同股同日可能有多个上榜原因。为避免无界text主键，使用本地ID；每次按交易日完整替换，供应方修改reason时不会留下旧记录。

## 5.8 stock_top_inst_daily

接口：[top_inst](https://tushare.pro/document/2?doc_id=107)。

```sql
detail_id bigint generated always as identity primary key -- 本地生成的龙虎榜营业部明细唯一标识
trade_date date not null -- 上榜交易日期
ts_code varchar(16) not null references stock(ts_code) -- 上榜股票的Tushare统一代码
exalter text not null -- 营业部名称
side smallint not null check (side in (0,1)) -- 榜单侧别：0为买入额前五，1为卖出额前五
buy numeric(24,4) null -- 该营业部买入额，单位为元
buy_rate numeric(14,6) null -- 买入额占股票总成交额比例，单位为百分比
sell numeric(24,4) null -- 该营业部卖出额，单位为元
sell_rate numeric(14,6) null -- 卖出额占股票总成交额比例，单位为百分比
net_buy numeric(24,4) null -- 该营业部净成交额，单位为元
reason varchar(512) not null -- 对应的上榜理由
synced_at timestamptz not null -- 本行最后同步时间
```

接口没有稳定行ID，也可能存在内容相同的合法行，因此不使用全字段哈希作为永久主键。按交易日完整、原子替换。

## 5.9 stock_limit_event_daily

接口：[limit_list_d](https://tushare.pro/document/2?doc_id=298)。

```sql
trade_date date not null -- 涨跌停或炸板事件所属交易日期
ts_code varchar(16) not null references stock(ts_code) -- 事件股票的Tushare统一代码
limit_type char(1) not null check (limit_type in ('U','D','Z')) -- 事件类型：U涨停、D跌停、Z炸板
industry varchar(64) null -- 股票所属行业
name varchar(64) null -- 股票名称
close numeric(20,6) null -- 当日收盘价
pct_chg numeric(14,6) null -- 当日涨跌幅，单位为百分比
amount_raw numeric(24,4) null -- 当日成交额；官方未标单位，保留源amount原值
limit_amount_raw numeric(24,4) null -- 板上成交金额；官方未标单位，保留源limit_amount原值
float_mv_raw numeric(24,4) null -- 流通市值；官方未标单位，保留源float_mv原值
total_mv_raw numeric(24,4) null -- 总市值；官方未标单位，保留源total_mv原值
turnover_ratio numeric(14,6) null -- 换手率，单位为百分比
fd_amount_raw numeric(24,4) null -- 封单金额；官方未标单位，保留源fd_amount原值
first_time time null -- 首次封板时间；跌停记录无该值
last_time time null -- 最后封板时间
open_times integer null -- 炸板次数；跌停记录表示开板次数
up_stat varchar(32) null -- 涨停统计，格式N/T表示T天内N次涨停
limit_times integer null -- 连续封板数量
synced_at timestamptz not null -- 本行最后同步时间
primary key (trade_date, ts_code, limit_type) -- 每只股票每日每类事件一条记录
```

官方未标注五个金额和市值字段的单位，所以最终设计先以_raw原值保存，禁止与统一“元”口径字段直接运算。上线前抽样与daily/daily_basic交叉确认后，再新增规范化视图；不能凭经验直接乘倍率。

## 5.10 stock_limit_step_daily

接口：[limit_step](https://tushare.pro/document/2?doc_id=356)。

```sql
trade_date date not null -- 连板数据所属交易日期
ts_code varchar(16) not null references stock(ts_code) -- 连板股票的Tushare统一代码
name varchar(64) null -- 股票名称
nums integer not null check (nums > 0) -- 连续涨停次数
synced_at timestamptz not null -- 本行最后同步时间
primary key (trade_date, ts_code) -- 每只股票每日一条连板记录
```

虽然nums与limit_times部分重复，但limit_step样例包含ST，不能由明确不含ST的limit_list_d完全替代。

