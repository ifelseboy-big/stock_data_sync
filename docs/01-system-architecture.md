# 系统架构与工程分层

## 1. 架构目标

系统不是“定时调用接口后直接写表”，而是一条可追溯、可重放、能够阻止半成品发布的数据生产链。正式数据必须能够追溯到采集批次、原始资产、加工任务和处理器版本。

系统遵守四条不变量：

1. 正式加工只能读取已经封存的原始资产或已经发布的数据集。
2. 采集批次关闭后不能重新打开，迟到数据进入新的修复批次。
3. 全系统同一时刻最多运行一个加工任务。
4. 消费者只能将 `dataset_release` 已发布的范围视为完整数据。

## 2. 总体架构

```text
Vue 管理端 ──HTTP──> FastAPI 管理 API ──读写控制面──> PostgreSQL
                                         │
APScheduler 计划器 ──创建批次和任务───────┤
                                         │
采集执行器 ──统一限流──> Tushare          │
    │                                    │
    └──原子封存──> Parquet 原始资产 ───────┤
                                         │
串行加工执行器 ──校验、转换、发布──────────> 正式业务表
                                         │
                                         └──更新 dataset_release

operations/system ──聚合运行表、数据库、调度器和主机状态──> 管理端
```

批次、任务、依赖、发布记录属于控制面，保存在 PostgreSQL；Parquet 原始资产和正式业务表属于数据面。日志用于定位单次执行问题，不能替代数据库中的任务状态和发布完整性记录。

| 组件 | 职责 | 禁止事项 |
| --- | --- | --- |
| Web 管理端 | 查询批次、任务、依赖、队列、发布和告警；提交受控人工操作 | 不连接数据库，不直接调用 Tushare |
| FastAPI | 查询模型、人工任务入口、健康检查和 API 指标 | 不执行长时间采集或加工 |
| Scheduler | 交易日门禁、阶段计划、任务派发、批次关闭、恢复协调 | 不把进程内状态作为任务真相 |
| Tushare 适配器 | SDK 隔离、字段选择、限流、超时、物理请求重试和指标 | 不写正式表，不执行跨接口关联 |
| 原始资产存储 | 原子封存不可变 Parquet，提供校验与重放 | 不保存单位换算或加工字段 |
| PostgreSQL | 正式数据、运行队列、依赖、发布、JobStore 和事务锁 | 不保存大块原始响应正文 |
| 运维观测 | 聚合运行状态、资源、告警和结构化日志 | 不参与任务状态恢复 |

## 3. 进程与部署边界

FastAPI 和 Scheduler 分进程运行，分别使用异步和同步 SQLAlchemy 会话。Scheduler 内部使用最多 4 个采集线程和固定 1 个加工线程，两个执行器不共用线程池。生命周期 advisory lock 保证只有一个 Scheduler，独立加工锁保护全局唯一加工槽位。

生产部署以 Mac mini 原生进程为唯一基线。PostgreSQL、API/Web 和 Scheduler 由 launchd 分别管理；数据、原始资产、备份、日志、应用和 Python 环境都落在用户首次安装时指定的统一目录。具体安装和启停方式以[发布与部署](06-deployment.md)为准。

不引入 Redis、Kafka、Celery 或 Airflow。当前单机模型由 PostgreSQL 队列、数据库锁和单调度进程保证一致性；如果未来扩展为多个执行节点，必须重新设计全局限流、任务租约和加工互斥，不能直接增加 Scheduler 副本。

## 4. 服务端目标分层

```text
src/server/app/
├── api/                     # API 路由装配和版本入口
├── catalog/                 # ApiSpec、DatasetSpec、发布时间和质量规则
├── common/                  # 无框架依赖的异常、分页和共享类型
├── core/                    # 配置与日志初始化
├── db/                      # Web 与 Scheduler 会话、ORM Base
├── integrations/            # Tushare 等外部系统适配器
├── storage/                 # RawAssetStore 与本地 Parquet 实现
├── modules/
│   ├── acquisition/         # 采集批次、任务、封存和重试
│   ├── processing/          # 依赖解析、串行队列、加工和发布
│   ├── partitions/          # 月分区创建与覆盖检查
│   ├── operations/          # 运维查询模型和人工操作
│   ├── stocks/              # 股票基础、行情、技术指标和资金流
│   ├── topics/              # 概念、热点、龙虎榜和涨跌停专题
│   ├── indices/             # 指数基础、行情、估值和权重
│   ├── etfs/                # ETF 基础、行情和规模
│   └── system/              # 健康、版本和配置摘要
├── scheduler/
│   ├── planners.py          # 阶段计划、批次计划和加工计划
│   ├── dispatchers.py       # 采集与加工任务领取
│   ├── recovery.py          # 启动恢复和周期协调
│   ├── runtime.py           # 执行器生命周期和线程容量
│   └── runner.py            # 唯一生产入口
├── observability/           # 指标、日志上下文和调度状态
└── cli/                     # 补采、回填、恢复和诊断
```

采集与加工分别由 `acquisition` 和 `processing` 承担，不设置同时包含两类状态和执行逻辑的通用 `tasking` 模块。

业务模块遵循 `api → service → repository → models` 单向依赖。`catalog` 只包含声明和纯规则；外部数据源必须经过 `integrations`；文件通过 `storage`；Tushare SDK 或 DataFrame 类型不能传入业务仓储层。页面、APScheduler job 函数也不能直接操作 Repository。

## 5. Web 分层

```text
src/web/src/
├── api/             # Axios 客户端与统一错误转换
├── components/      # 通用状态、指标、图表和页面组件
├── composables/     # 可复用的异步资源与交互逻辑
├── layouts/         # 管理端布局
├── modules/         # dashboard/acquisition/processing/dependencies/providers/operations/system
├── plugins/         # ECharts 等第三方库注册
├── router/          # 路由装配
├── stores/          # 确实需要跨页面共享的状态
├── styles/          # 设计变量与基础样式
├── types/           # 跨模块类型
└── utils/           # 可独立测试的纯函数
```

页面按业务能力组织，不能直接调用 Axios；模块 API 负责传输，查询契约与执行域对象分离。Store 不保存普通页面请求状态，避免重复缓存和数据过期。

## 6. 实施顺序

1. 数据库迁移：运行表、业务表、月分区、索引和数据库角色。
2. 声明与原始层：ApiSpec、DatasetSpec、Parquet 封存和哈希校验。
3. 采集链路：阶段计划、批次任务、分页拆分、重试、关闭和恢复。
4. 加工链路：依赖解析、全局串行队列、正式表转换和原子发布。
5. 管理入口：运维查询、追溯详情、补采、回填和状态迁移。
6. 生产化：告警、备份、恢复演练、容量与性能压测。

每个数据集只有在采集、加工、发布、监控和恢复路径全部通过后才算交付，不能上线半条链路。
