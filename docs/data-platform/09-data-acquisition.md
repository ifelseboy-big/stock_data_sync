<!-- 来源：https://tcnq6fudd3wh.feishu.cn/docx/WoYqdWMeJoqcOtxUVJxccyjenTe；飞书修订版：88 -->

# 6. 数据采集设计

采集任务只解决“从哪个接口、按什么范围、得到一份完整原始资产”。每个接口的字段、拆分、空结果和依赖规则集中在代码注册表，不散落在定时函数中。

| ApiSpec属性 | 含义 |
|-|-|
| api_name、provider | Tushare接口名和供应方标识 |
| fields | 明确请求的源字段及顺序，用于schema指纹和变更检测 |
| schedule_group | MASTER、DAILY、HOT、DELAYED或BACKFILL阶段 |
| scope_builder | 根据交易日、股票、板块、题材、指数和月份生成请求范围 |
| split_policy | 按状态、交易所、代码、板块、题材、指数或offset拆分 |
| row_limit | 接口单次上限；达到上限时必须继续拆分或分页 |
| empty_policy | ALLOWED、RETRY_UNTIL_CUTOFF、FORBIDDEN或UNSUPPORTED |
| retry_policy | 最大尝试次数、退避、截止时间和可重试错误分类 |
| date_extractor | 校验返回记录的业务日期是否属于任务范围 |

**任务生成。**计划器把一个ApiSpec展开为多个collection_task。scope_key使用可读且确定的规范，例如trade_date=2026-07-20、exchange=SSE|status=L、theme_code=000053.DC或index_code=000300.SH|month=2026-07。request_params保存排序后的完整请求参数；同一批次内依靠unique(batch_id, api_name, scope_key)防止重复任务。

```text
PENDING ──领取──> RUNNING ──完整并有数据──> SUCCESS
                         ├──合法空结果──────> EMPTY_VALID
                         ├──可恢复异常──────> RETRY_WAIT ──到期──> PENDING
                         ├──不可恢复异常────> FAILED
                         ├──人工跳过────────> SKIPPED
                         └──取消────────────> CANCELLED
```

**领取和执行。**派发器在短事务中使用FOR UPDATE SKIP LOCKED领取到期任务并更新RUNNING，提交后再调用Tushare。最多4个采集线程并发；所有TushareProvider实例共享进程级平滑限流器。全局默认预算为480次/分钟并可按账户配置；ApiSpec可设置更低的接口预算、日配额和截止时间，每个物理请求必须同时通过全局与接口预算。请求超时默认30秒，网络异常最多3次，指数退避并带随机抖动。

**完整性检查。**请求成功后校验字段集合、字段顺序、业务日期、代码范围、自然键重复、分页连续性和返回行数。返回行数等于上限一律视为疑似截断；先进一步拆分，再封存资产。stock_basic和etf_basic按状态及交易所拆分；ths_member按板块；dc_concept_cons按theme_code；index_daily和index_weight按配置指数；fund_adj按offset或ETF代码分片。

**错误分类。**连接超时、临时限流、服务端异常和数据尚未发布进入RETRY_WAIT；Token失效、权限不足、参数错误、未知字段变化和无法解析的schema直接FAILED并触发人工告警。字段变化时仍可封存完整返回文件用于排查，但collection_task不得成功，依赖解析也不得把该资产标为READY。

**禁止事项。**采集任务不连接正式业务表做写入，不进行跨接口join，不计算MACD，不换算单位，不因为HTTP 200直接判定成功。人工补数只能创建REPAIR或BACKFILL批次，API请求线程不得同步等待采集完成。

