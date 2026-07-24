# Tushare 采集设计

## 1. 职责边界

```text
阶段计划器
   ↓ 根据 ApiSpec 展开范围
collection_task
   ↓
TushareProvider：字段选择、限流、超时、物理请求重试和指标
   ↓
Tushare API
   ↓
完整性检查与 Parquet 原子封存
```

采集任务只表达接口、请求范围和完整性要求。限流、超时和物理请求重试统一位于 `integrations` 的 Provider 层；跨接口关联、单位换算和正式表写入属于加工层。

每个接口通过 `ApiSpec` 集中声明，不能把接口规则散落在 APScheduler job 中：

| 属性 | 含义 |
| --- | --- |
| `api_name`、`provider` | 接口名和供应方 |
| `fields` | 明确请求的源字段及顺序，用于结构指纹 |
| `schedule_group` | MASTER、DAILY、HOT、DELAYED 或 BACKFILL |
| `scope_builder` | 根据日期、证券、板块、题材、指数或月份生成请求范围 |
| `split_policy`、`row_limit` | 拆分、分页和疑似截断处理 |
| `empty_policy` | ALLOWED、RETRY_UNTIL_CUTOFF、FORBIDDEN 或 UNSUPPORTED |
| `retry_policy` | 尝试次数、退避、截止时间和错误分类 |
| `date_extractor` | 返回数据的业务日期校验 |

## 2. 限流策略

1. Tushare 账户硬限制按 500 次/分钟配置，应用默认预算为 480 次/分钟，保留安全余量。
2. 采用平滑限流，每约 125ms 释放一个全局请求槽，不在分钟边界突发发送。
3. Scheduler 最多同时运行 4 个采集任务；所有线程和 Provider 实例共享同一个进程级额度。
4. `ApiSpec` 可以声明更低的接口预算和日配额，每个物理请求必须同时取得全局及接口请求槽。
5. Scheduler 使用 PostgreSQL advisory lock 保证单实例，避免多个进程分别计算 480 次预算。
6. 手工补采、历史回填和自动任务使用同一数据库队列及同一额度，Web API 不直接调用 Tushare。
7. 5 秒采集派发只负责启动和兜底唤醒；任一采集任务完成后立即补足空闲线程，使有积压时持续使用请求预算。

当前限流器以单 Scheduler 进程为边界。如果以后部署多个执行节点，必须改为 PostgreSQL 全局配额或专用分布式限流方案，不能直接增加实例。独立开发环境与部署环境共用同一 Token 时无法通过各自数据库协调，开发环境必须显式降低预算，为部署环境预留额度。

## 3. 超时与两级重试

物理请求层处理连接超时、临时网络错误和供应方 5xx。单次请求默认超时 30 秒，最多尝试 3 次，使用指数退避和随机抖动；每次重试都必须重新申请请求额度。

任务层处理“数据尚未发布”和供应方返回的分钟限流等跨分钟问题。任务进入 `RETRY_WAIT` 并持久化下次时间，不占用采集线程；历史回填和修复不受历史业务日的日任务截止时间限制。人工回填包含当天且接口尚未到发布时间时，即使常规尝试次数已经用完，也必须至少保留一次截止时刻重试，不能在盘前提前形成最终失败。Token 失效、权限不足、参数错误、未知字段或结构变化属于不可恢复错误，直接失败并告警。

这两级不能混用：短暂网络抖动不创建新的业务任务；业务数据未就绪不能在 Provider 内长时间阻塞。

## 4. 完整性与原始资产

HTTP 200 不代表采集成功。每个任务必须校验字段集合和顺序、业务日期、代码范围、自然键重复、分页连续性和返回行数。返回行数等于接口上限时一律视为疑似截断，继续拆分或分页；无法证明完整时不能进入成功状态。

空结果按接口和业务日期判定：合法空结果封存零行 Parquet 并记为 `EMPTY_VALID`；按规则应有数据但为空时进入任务重试；固定起始日期之前的请求记为 `UNSUPPORTED`。具有滚动历史窗口的接口，在窗口外真实返回空结果时封存零行资产并记为 `EMPTY_VALID`，同时由接口规范记录窗口长度，避免把供应方不再提供的历史范围反复重试成故障。

成功任务必须先在本次 `execution_token` 隔离的路径完成 Parquet 文件写入、`fsync`、哈希与结构校验、原子改名，再在短数据库事务中校验当前任务仍为同一执行租约，随后登记 `raw_data_asset` 和更新任务状态。超时回收后的旧 worker 不得覆盖新尝试文件或登记资产。字段变化时可以保留原始文件用于排查，但不能将资产解析为 READY。

## 5. 幂等和禁止事项

`scope_key` 必须可读、稳定并唯一表达本次范围；同一批次通过 `(batch_id, api_name, scope_key)` 防止重复任务。分页或拆分结果最终合并为该任务唯一的原始资产。

采集任务不得写正式业务表、执行跨接口 Join、计算技术指标、换算单位，或因为请求成功直接判定任务成功。API 请求线程不得同步等待补采或回填完成。

## 6. 观测口径

指标必须区分物理请求耗时、逻辑查询总耗时、限流等待、重试次数、空结果和物理请求成功率。接口耗时不包含限流器排队时间；任务耗时单独记录从领取到封存终态的墙钟时间。

## 7. 接口与数据集依赖

接口依赖分为采集范围依赖和正式加工依赖。主数据已经存在后，同一批次的原始接口可以并行采集；正式加工必须等待所有必要资产就绪。

| 输出数据集 | 必需原始接口 | 主数据或加工依赖 | 发布规则 |
| --- | --- | --- | --- |
| `stock` | `stock_basic`：L/D/P/G，按交易所拆分 | 无 | 完整集合合并后发布 |
| `stock_company` | `stock_company` | `stock` | 只接受 `stock` 中存在的代码 |
| `stock_daily.core` | `daily`、`daily_basic`、`adj_factor` | `stock`、`trade_calendar` | 只发布最新股票主表中的非退市代码；范围外行告警并过滤 |
| `stock_daily.limit` | `stk_limit` | 已发布的 `stock_daily.core`、`stock` | 只补充核心日线已有行；零匹配允许成功 |
| `stock_technical_daily` | `stk_factor` | `stock`；`stock_daily` 只做核对 | 过滤主表范围外代码，保持 Tushare 历史快照语义 |
| `stock_moneyflow_daily` | `moneyflow` | `stock`、`trade_calendar` | 过滤主表范围外代码后按交易日发布 |
| `ths_board_moneyflow_daily` | `moneyflow_cnt_ths`、`moneyflow_ind_ths` | `trade_calendar` | 两个接口可并行；按 `board_type + board_name + trade_date` 发布，供应方板块代码允许为空 |
| `concept_board` | `ths_index` | 无 | 筛选 `exchange=A`、`type=N` |
| `concept_board_daily` | `ths_daily` | `concept_board` | 只发布概念主表代码；非目标类型计入告警 |
| `concept_board_member` | `ths_member`，按板块完整获取 | `concept_board`、`stock` | 只发布 `is_new=Y` 当前快照，按板块完整替换 |
| `theme_index` | `ths_index` | 无 | 筛选 `exchange=A`、`type=TH`，与概念分表发布 |
| `theme_index_daily` | `ths_daily` | `theme_index` | 只发布主题指数主表代码；零目标行按成功空发布处理 |
| `theme_index_member` | `ths_member`，按主题完整获取 | `theme_index`、`stock` | 只发布 `is_new=Y` 当前快照，按主题完整替换 |
| `stock_hot_rank_daily` | `ths_hot`、`dc_hot` | `stock`、`trade_calendar` | 当日任务使用最终榜；历史 THS 使用 `is_new=N` 获取盘中和盘后快照，两路接口都在加工层选择最新完整快照 |
| `market_theme_daily` | `dc_concept` | `trade_calendar` | 发布东方财富每日动态题材，不与同花顺主题指数混用 |
| `market_theme_member_daily` | `dc_concept_cons` | `market_theme_daily` 发布完成、`stock` | 前者只作为加工顺序依赖；供应方滚动窗口外没有同日题材排行时，成员仍可按题材代码发布 |

v0.1.21 使用 `20260720_0011` 对上述两项供应方兼容规则做保留数据迁移：板块资金业务身份不再依赖可能为空的供应方代码，题材成员不再以同日排行记录作为数据库外键。迁移只调整约束和索引，不删除历史业务行。
| 龙虎榜 | `top_list`、`top_inst` | `stock`、`trade_calendar` | 两个正式表分别按交易日原子替换 |
| 涨跌停与连板 | `limit_list_d`、`limit_step` | `stock`、`trade_calendar` | 分别发布，不互相推导 |
| `market_index_daily` | `index_daily` | `market_index`、目标指数配置 | 按指数代码获取 |
| `index_daily_basic` | `index_dailybasic` | `market_index` | 仅发布官方支持指数 |
| `market_index_weight` | `index_weight` | `market_index`、`stock` | 按指数、月份发布快照 |
| `etf_daily` | `fund_daily`、`fund_adj` | `etf`、`trade_calendar` | 只保留 `etf` 主表代码 |
| `etf_share_size_daily` | `etf_share_size` | `etf`、目标交易日 D | D+1 独立发布，不阻塞 `etf_daily` |

股票接口中的显式代码别名采用统一身份规则：现代码与旧代码同时存在时保留现代码原始行，只出现旧代码时映射到现代码。`stock_daily.core` 的 `change`、`pct_chg` 由 `close`、`pre_close` 确定性计算；供应方冗余字段不一致只产生数据质量警告，不形成无法恢复的整批失败。

如果以后本地计算 MACD 等指标，应新增本地计算数据集，其依赖为完整 `stock_daily` 历史、计算规则版本和复权锚点，不能覆盖直接同步的 `stock_technical_daily`。

板块、主题和股票主数据是成分加工的执行前置条件，不是单独的重算触发器。只有按最新主表代码完整采集的新 `ths_member` 原始批次才创建成分加工任务，防止历史实体包缺少新增代码时产生不完整快照。

## 8. 每日调用时间线

时间均为 `Asia/Shanghai`。官方未明确发布时间的接口，以建议触发时刻配合空结果重试和补采机制吸收延迟。

| 时间 | 流程 | 关键规则 |
| --- | --- | --- |
| 系统启动或每月 | 检查本地 `trade_calendar` 是否覆盖当前年份和下一年份 | 日常接口只查本地日历 |
| 每月 1 日 08:40 | 调用 `ths_index(type=N/TH)`，分别刷新同花顺概念和主题指数主表 | 同一原始接口按类型拆分范围，正式数据分表发布 |
| 每月 1 日 10:00 | 按全部同花顺概念和主题代码调用 `ths_member` | 两类成员共用限流和原始资产，分别按各自主表过滤发布 |
| 每日 08:45 | 处理 `etf_share_size(D-1)` 及延迟补采；交易日采集 `stk_limit(D)` | ETF 份额即使休市也要执行 |
| 交易日 09:25 | 采集 `adj_factor(D)` | 原始资产先落地，正式 `stock_daily` 等待盘后接口 |
| 交易日 16:10 | 创建 DAILY 批次，采集 `daily`、`fund_daily` 和配置内指数行情 | 空结果不能立即视为成功 |
| 交易日 17:30 | 采集 `daily_basic`、`moneyflow`、`fund_adj`、指数基本指标、龙虎榜、涨跌停、连板、停复牌、概念行情和板块资金 | 使用延迟重试处理供应方未完成发布 |
| 交易日 19:00–21:00 | 采集 `stk_factor`、`dc_concept`，并按交易日分页采集 `dc_concept_cons` | 成员接口使用 3000 行分页直到末页；原始响应完整保留，加工时仅去除完全重复行，键冲突直接失败 |
| 批次任务全部终态 | 关闭批次、封存资产、解析依赖并生成加工计划 | 允许部分失败关闭；缺失依赖的加工任务进入 BLOCKED |
| 加工阶段 | 从统一入口受控并发加工并发布 | DATE 数据集按输出数据集和业务日期互斥，其他范围按输出数据集互斥；加工失败重试不再调用 Tushare |
| 交易日 22:35 | 创建 HOT 批次，调用 `ths_hot(is_new=Y)`、`dc_hot(is_new=Y)` | 原始返回完整封存；加工选择最新完整快照 |
| 次日 08:45 起 | 继续检查 `etf_share_size(D)` 和海外 ETF 延迟数据 | 创建 DELAYED 或 REPAIR 批次，不重新打开原批次 |

休市日跳过当日 A 股行情、题材、龙虎榜和热榜批次，但仍执行交易日历补全、主数据刷新、上一交易日延迟数据、自动补采和人工历史回填。

历史回填先由本地 `trade_calendar` 枚举开市日，再按业务日期创建 BACKFILL 批次。接口可以声明独立的历史请求范围；`ths_hot` 历史任务使用 `is_new=N`，因为实测指定旧交易日时 `is_new=Y` 返回空，而 `N` 返回全部盘中和盘后快照。不同数据集起始日期和历史保留窗口不同：固定起始日期之前记为 `UNSUPPORTED`；滚动窗口外的真实空响应记为 `EMPTY_VALID` 零行资产，使同批次下游可以得到明确的空发布，而不是持续重试。

## 9. 接口拆分规则

| 接口类型 | 拆分策略 |
| --- | --- |
| `stock_basic`、`etf_basic` | 按 `list_status` 和 `exchange` 拆分，合并后去重并核对状态集合 |
| `daily`、`daily_basic`、`adj_factor`、`moneyflow` | 优先按交易日获取全市场；达到或接近上限时按股票主数据拆分代码范围 |
| `stk_limit` | 使用 `limit/offset` 连续分页并跨页校验自然键，完整封存后再按已发布核心股票范围过滤，不能截断单页结果 |
| `ths_member` | 合并 `type=N` 概念代码和 `type=TH` 主题代码，逐代码采集；加工时分别按concept_board和theme_index过滤发布 |
| `dc_concept_cons` | 按交易日使用 `limit/offset` 连续分页；返回满 3000 行必须继续取下一页并合并为一个原始资产。供应方跨页完全重复行在加工层确定性去重，内容冲突失败；当前分页资产不得与旧逐题材范围跨批次合并 |
| `ths_hot` | 当日 HOT 使用 `is_new=Y`；BACKFILL/REPAIR 使用 `is_new=N`。历史多时点原始行以 `trade_date + data_type + ts_code + rank_time` 标识并全部封存；加工按 `rank_time` 的分钟快照分组，以最大行数识别完整快照并选择最新一组 |
| `dc_hot` | `is_new=Y` 仍可能返回同一榜单的多个 `rank_time`。原始层不按股票去重；加工层按时点分组，以最大快照行数判断完整性，再选择时间最新的完整组；组内股票和名次都必须唯一 |
| `moneyflow_cnt_ths` | 原始自然键使用 `trade_date + name`；供应方 `ts_code` 允许为空，禁止虚构代码。正式表同样以板块类型、名称和日期作为业务键，代码仅为可空属性 |
| `index_daily`、`index_weight` | 按配置内 `index_code` 获取；权重按指数和月份获取 |
| `fund_adj` | 使用 `offset/limit` 分页或按 ETF 代码分批，再与 ETF 主表核对 |
| 热榜 | 按 `source`、`market_type`、`rank_type` 拆分；THS 和 DC 都从各自原始结果选择最新完整快照，股票和名次必须分别唯一 |

## 10. 跨接口校验

| 校验 | 处理 |
| --- | --- |
| `daily.close` 与 `daily_basic.close` | `daily` 是行情价格事实来源，`daily_basic` 只提供估值派生字段。沪深缺失或实质冲突超过阈值时阻塞发布；北交所历史 `daily_basic` 已确认存在分段缺失、旧代码映射后价格错位和前收盘快照，异常行必须隔离估值字段并告警，但不得丢弃同日 `daily + adj_factor` 已验证的行情记录 |
| `daily.pre_close` 与 `stk_limit.pre_close` | 只核对，不重复存列 |
| `fund_daily` 代码 | 只允许 ETF 主表中存在的代码进入 `etf_daily` |
| 股票事实代码 | 只保留最新股票主表中的非退市代码；范围外记录计入任务拒绝数和警告，不触发无效的主表刷新 |
| 题材、概念和主题成员代码 | 必须存在于最新股票主表；范围外成员计入拒绝数，由下一次完整成员快照覆盖 |
| 热榜排名 | 同一榜单 `rank` 唯一且为正整数，`rank_time` 必须属于最终批次 |
| 成交量和金额 | 换算前后保留任务级统计，抽样反算必须与源值一致 |

## 11. 供应方能力边界

| 接口或数据集 | 明确边界 | 系统处理 |
| --- | --- | --- |
| `ths_member` | `weight`、`in_date`、`out_date` 当前不可用 | 只表达当前观察快照，不生成真实成员有效期 |
| `ths_daily` | 单日响应可混合概念、行业和其他同花顺指数，且历史日期可能没有任何当前概念/主题代码 | 分别按概念和主题主表过滤；非目标类型不发布，目标集合为空也登记成功发布 |
| `ths_index(type=TH)` | 当前仅返回少量稳定主题指数，实测为10个 | 作为独立主题指数数据集，不替代东方财富每日动态题材 |
| `dc_concept` | 实际查询表现为约3个自然月的滚动历史窗口；2026-07-20验证时，2026-04-17起有数据，更早日期为空 | BACKFILL/REPAIR窗口外空响应封存为 `EMPTY_VALID`；窗口内继续按必要数据重试 |
| `dc_concept_cons` | 2026-02-03起可返回题材成员，历史范围可能早于同日 `dc_concept` 排行 | 完整保存成员；同日题材排行因滚动窗口不可用时，不伪造题材主记录，也不丢弃成员关系 |
| `limit_list_d` | 数据从 2020 年开始且不含 ST 股票 | 不能作为全量涨跌停历史真值 |
| `limit_step` | 官方未明确交易所覆盖范围；2026-07-13 至 2026-07-17 实测包含 ST，但未返回北交所股票，同期 `limit_list_d` 正常返回北交所涨停记录 | 保留独立供应方口径，不标记为全市场完整连板榜；持续观察北交所覆盖情况 |
| `limit_list_d` 金额和市值 | 官方未明确五个字段单位 | 以 `_raw` 字段保存，验证前不进入统一金额计算 |
| `index_weight` | 官方定义为月度数据 | 使用 `snapshot_date`，不解释为每日生效权重 |
| `index_dailybasic` | 只覆盖官方列出的少数大盘指数 | 与指数行情并存，不能替代指数行情 |
| `index_daily` | 不覆盖申万指数 | 指数主表范围与可用行情范围分别管理 |
| `stk_factor` | 历史前复权为历史当日快照 | 保留源快照语义，本地动态复权另建数据集 |
| `daily.ah_vol`、`daily.ah_amount` | 2026-07-06 起有数据 | 此前保持 `NULL` |
| `trade_cal` | 公开参数明确支持 SSE、SZSE，未明确 BSE | A 股日流程以本地 SSE 日历为统一门禁 |
| `etf_basic.mgt_fee` | 官方未明确数值单位 | 保存原值并标记待验证，不参与费率计算 |

按当前接口范围，生产账号需要至少 8000 积分档，主要门槛来自 ETF 基础与份额规模、DC 热榜和连板等接口；上线前仍需按账号实际权限复核。权限不足属于不可恢复错误，任务直接失败并告警。

## 12. 当前实施状态

31 个接口已经全部进入统一 `ApiSpec` 目录；`ths_index` 按 `N` 和 `TH` 两个范围采集并分别发布概念与主题指数，东方财富动态题材继续独立发布。交易日门禁、完整采集批次、依赖解析、22:30 热榜、D+1 ETF 补录、动态板块/题材拆分和指数月度权重均已落在独立的 `acquisition` 与 `processing` 模块。生产任务与历史回补共用同一采集、限流、Parquet、依赖解析和受控并发加工链路。
