<!-- 来源：https://tcnq6fudd3wh.feishu.cn/docx/NJXndRs7eoRq7KxWPQ0cCyycnPc；飞书修订版：78 -->

# 8. Tushare接口依赖关系

接口“依赖”分为两类：采集范围依赖和正式加工依赖。主数据已经存在后，同一批次的原始接口通常可以并行采集；需要严格等待的是正式数据加工和发布，不应把所有Tushare调用机械串行化。

| 输出数据集 | 必需原始接口 | 主数据/加工依赖 | 发布规则 |
|-|-|-|-|
| stock | stock_basic：L/D/P/G，按交易所拆分 | 无 | 完整集合合并后发布 |
| stock_company | stock_company | stock | 只接受stock中存在的代码 |
| stock_daily核心 | daily + daily_basic + adj_factor | stock、trade_calendar | 三个资产全部READY后原子发布 |
| stock_daily涨跌停补充 | stk_limit | 已发布的stock_daily核心、stock | 只补up_limit/down_limit；单独发布完成状态 |
| stock_technical_daily | stk_factor | stock；stock_daily只做核对 | 保持Tushare历史快照语义 |
| stock_moneyflow_daily | moneyflow | stock、trade_calendar | 按交易日发布 |
| ths_board_moneyflow_daily | moneyflow_cnt_ths + moneyflow_ind_ths | trade_calendar；概念记录与concept_board核对 | 两个接口可并行，分别按board_type发布 |
| concept_board | ths_index | 无 | 筛选exchange=A、type=N |
| concept_board_daily | ths_daily | concept_board | 代码不存在时阻塞，不静默丢弃 |
| concept_board_member | ths_member，按板块完整获取 | concept_board + stock | 只发布is_new=Y当前快照，按板块完整替换 |
| stock_hot_rank_daily | ths_hot + dc_hot，is_new=Y | stock、trade_calendar | 22:30最终批次单独发布 |
| market_theme_daily | dc_concept | trade_calendar | 按交易日发布 |
| market_theme_member_daily | dc_concept_cons | market_theme_daily + stock | 原始接口可并行采集；加工时先保证父题材存在 |
| 龙虎榜 | top_list + top_inst | stock、trade_calendar | 两接口可并行，两个正式表分别按交易日原子替换 |
| 涨跌停/连板 | limit_list_d + limit_step | stock、trade_calendar | 分别发布，不互相推导 |
| market_index_daily | index_daily | market_index、目标指数配置 | 按指数代码获取 |
| index_daily_basic | index_dailybasic | market_index | 仅发布官方支持指数 |
| market_index_weight | index_weight | market_index + stock | 按指数、月份发布快照 |
| etf_daily | fund_daily + fund_adj | etf、trade_calendar | 只保留etf主表代码 |
| etf_share_size_daily | etf_share_size | etf；目标交易日D | D+1独立发布，不阻塞etf_daily |

如果未来选择本地计算MACD等指标，则新增一个本地计算数据集，其依赖改为“完整stock_daily历史 + 计算规则版本 + 复权锚点”。该数据集不能覆盖当前直接同步的stock_technical_daily。

# 9. 每日调用时间线

下表时间均为Asia/Shanghai。标有“官方”的时间来自Tushare文档；其余是项目建议触发时间，实际应通过空结果重试和补采机制吸收供应方延迟。

| 时间 | 流程 | 关键规则 |
|-|-|-|
| 系统启动/每月 | 检查本地trade_calendar是否覆盖当前年份和下一年份；缺失时同步SSE/SZSE。 | 日常接口先查本地日历，不为每个任务重复调用trade_cal。 |
| 每日08:45 | 处理etf_share_size(D-1)及其他延迟补采；若今天是交易日，再采集stk_limit(D)。 | ETF份额是上一交易日数据，即使今天休市也要执行。stk_limit官方约08:40更新。 |
| 交易日09:25 | 采集adj_factor(D)。 | 官方约09:15～09:20完成；原始资产可先落地，正式stock_daily仍等盘后接口。 |
| 交易日16:10 | 创建DAILY采集批次，先采daily、fund_daily和已配置指数行情。 | daily官方15:00～16:00入库；空结果不能立即视为成功。 |
| 交易日17:30 | 采集daily_basic、moneyflow、fund_adj、index_dailybasic、龙虎榜、涨跌停、连板、停复牌、概念行情和板块资金。 | daily_basic官方15:00～17:00入库；没有明确官方时刻的接口按“盘后建议时刻+延迟重试”处理。 |
| 交易日19:00 | 采集stk_factor、dc_concept、dc_concept_cons；刷新ths_member完整快照。 | 概念成员按板块拆分；题材成员按theme_code拆分。 |
| 批次所有任务终态 | 关闭批次、封存raw_data_asset、解析依赖、生成加工计划。 | 允许部分失败关闭；依赖完整的加工任务继续，缺失依赖的任务BLOCKED。 |
| 加工阶段 | 通过全局串行入口依次执行正式表加工。 | 采集仍可继续并行；加工失败重试不再调用Tushare。 |
| 交易日22:35 | 单独创建HOT批次，调用ths_hot/DC_hot，参数is_new=Y。 | 校验rank_time属于22:30最终版本后发布。 |
| 次日08:45起 | 继续检查etf_share_size(D)，海外ETF按配置延迟补采。 | 形成DELAYED或REPAIR批次，不重新打开D日原批次。 |

**休市日规则：**跳过以当天D为业务日期的A股行情、题材、龙虎榜和热榜批次；仍执行交易日历补全、主数据刷新、上一交易日延迟数据、自动补采和人工历史回填。

**主数据规则：**stock、etf、market_index、concept_board应在日事实首次上线前完成全量初始化。之后stock/etf可每日盘后增量刷新，company和index主数据可低频刷新，concept_board及成员因用户重点关注，建议每个交易日盘后刷新。

# 10. 完整性、重试与历史补采

## 10.1 采集成功判定

HTTP或SDK调用成功不等于数据采集成功。每个任务至少校验接口字段集合、业务日期、代码范围、主键重复、返回行数和分页完整性。返回行数等于接口最大限制时必须视为疑似截断，继续拆分或分页；无法证明完整时任务不能进入SUCCESS。

| 接口类型 | 拆分策略 |
|-|-|
| stock_basic、etf_basic | 按list_status和exchange拆分，合并后去重并核对状态集合。 |
| daily、daily_basic、adj_factor、moneyflow | 优先按交易日获取全市场；达到上限或接近上限时根据stock主数据拆分代码范围，不能依赖单次全市场结果。 |
| stk_limit | 接口含A/B股和基金且上限5800。达到上限时必须按目标股票代码拆分；不得先截断再过滤。 |
| ths_member | 先从concept_board取得板块代码，再逐板块采集，完整后发布当前成员快照。 |
| dc_concept_cons | 按theme_code拆分；单次3000行上限不能覆盖全市场题材成员。 |
| index_daily、index_weight | 按已配置index_code获取；权重按指数和月份获取。 |
| fund_adj | 使用offset/limit分页或按etf代码批次获取，再与etf主表内连接。 |
| 热榜 | 按source、market_type、rank_type拆分；只接收is_new=Y最终版本。 |

## 10.2 空结果和重试

空结果必须按数据集分类：休市日行情属于允许为空；top_list等事件型接口在交易日也可能合法为空；daily、daily_basic、最终热榜等按规则应有数据但为空时属于可恢复失败。网络异常、限流、数据尚未发布进入RETRY_WAIT；Token、权限、参数或字段结构变化直接转人工处理。

原批次关闭后出现的迟到数据通过新的REPAIR批次处理。补采成功只解除受影响加工任务的阻塞，不重跑无关数据。

## 10.3 幂等与供应方修订

有稳定自然键的日表使用事务内upsert并记录新的processing_task；快照型、无稳定源行ID或可能修订文本的表使用“按业务日期/板块删除旧版本，再完整插入新版本”的原子替换方式。适用表包括concept_board_member、stock_hot_rank_daily、stock_top_list_daily、stock_top_inst_daily和market_theme_member_daily。

## 10.4 跨接口校验

| 校验 | 处理 |
|-|-|
| daily.close 与 daily_basic.close | 允许精度差，不允许实质冲突；冲突则阻塞stock_daily发布。 |
| daily.pre_close 与 stk_limit.pre_close | 只核对，不重复存列。 |
| fund_daily代码 | 只允许etf主表中存在的代码进入etf_daily。 |
| 题材/概念成员代码 | 成员股票必须存在于包含L/D/P/G的stock主表；否则先刷新主数据。 |
| 热榜排名 | 同一榜单rank唯一且为正整数；rank_time必须属于最终批次。 |
| 成交量和金额 | 换算前后保留任务级统计，抽样与源值反算一致。 |

## 10.5 历史回填

历史回填先从本地trade_calendar枚举请求区间内的开市日，再按业务日期创建BACKFILL批次；不能用“今天是否开市”判断历史任务。主数据使用批次关闭时最新有效版本。不同数据集起始日期不同，超出Tushare覆盖范围的日期记录为UNSUPPORTED，而不是空数据成功。

# 11. Tushare能力边界

| 接口/数据集 | 明确边界 | 数据库处理 |
|-|-|-|
| ths_member | weight、in_date、out_date当前标为“暂无”。 | 只表达当前观察快照；不生成真实成员有效期。 |
| dc_concept、dc_concept_cons | 数据从2026-02-03开始。 | 更早日期标记UNSUPPORTED，不能声称具有完整历史题材库。 |
| limit_list_d | 数据从2020年开始，且不含ST股票。 | 保留覆盖声明；不能作为全量涨跌停历史真值。 |
| limit_list_d金额/市值 | 官方没有标明五个字段单位。 | 以_raw字段保存，抽样确认前不进入统一金额计算。 |
| index_weight | 官方定义为月度数据。 | 使用snapshot_date，不解释为每日生效权重。 |
| index_dailybasic | 只覆盖官方列出的少数大盘指数。 | 与market_index_daily并存，不能替代指数行情。 |
| index_daily | 不覆盖申万指数。 | market_index主表范围与可用行情范围分别管理。 |
| stk_factor | 历史前复权为历史当日快照，不按当前锚点更新。 | 保留源快照语义；本地动态复权另建数据集。 |
| daily.ah_vol/ah_amount | 官方注明从2026-07-06开始有数据。 | 此前保持NULL。 |
| trade_cal | 公开文档明确列出SSE/SZSE，未明确BSE。 | A股日流程以本地SSE日历为统一门禁，不伪造BSE源记录。 |
| etf_basic.mgt_fee | 官方未明确数值单位。 | 保存原值并标注待验证，暂不参与费率计算。 |

按本稿所用接口，生产账号需要至少8000积分档，主要门槛来自ETF基础和份额规模、DC热榜、连板等接口。Tushare足以支撑本项目的盘后研究数据，但上述覆盖缺口不能通过增加数据库字段消除。

# 12. 待确认结论

请按下面项目确认。确认后，数据库DDL、SQLAlchemy模型和调度任务应严格以本稿为准，不再按接口机械拆表或临时增加业务字段。

- [ ] 确认最终采用25张业务表，股票、ETF、指数继续分开建模。

- [ ] 确认保留6张系统运行表，用于批次、依赖、原始资产、重试和正式发布。

- [ ] 确认stock_daily合并daily、daily_basic、adj_factor，并只增加stk_limit的up_limit/down_limit。

- [ ] 确认stock_technical_daily保存Tushare stk_factor快照，不与本地动态复权混用。

- [ ] 确认concept_board_member只表示最近完整采集的当前成员，不承诺真实纳入/剔除历史。

- [ ] 确认热榜只保存is_new=Y的22:30最终A股榜。

- [ ] 确认ETF份额规模保持独立表，D+1发布，不阻塞etf_daily。

- [ ] 确认limit_list_d未标单位字段先保存_raw原值，验证后再提供统一金额视图。

- [ ] 确认业务金额统一元、成交量统一股/份、百分比保留Tushare百分数口径。

- [ ] 确认本期不包含实时、分钟、财务、周线和月线接口。

- [ ] 确认stock_daily、stock_technical_daily、stock_moneyflow_daily和market_theme_member_daily按trade_date月分区，并采用第2章列出的索引清单。

当前项目调度器和tasking模块仍未实现本稿中的交易日门禁、采集批次、依赖解析、22:30热榜及D+1补录。文档确认后再进入DDL和任务实现，避免设计与代码再次分叉。

