# 本机 MCP 只读服务

MCP 通过本机 stdio 访问同机 PostgreSQL，只允许查询正式业务数据。不监听网络端口，
不经过 Web API，不提供原始 SQL 工具，也不能复用应用写入账号。

## 固定工具

| 工具 | 能力 | 单次上限 |
| --- | --- | ---: |
| `search_securities` | 按代码或名称搜索股票、ETF、指数 | 50 |
| `get_stock_snapshot` | 股票行情、估值、技术指标、资金流快照 | 1只股票 |
| `get_stock_history` | 原始/前复权/后复权行情，可附资金流 | 1000 |
| `list_screen_fields` | 返回选股字段目录和操作符 | 固定目录 |
| `screen_stocks` | 白名单字段组合筛选、股票池、排序 | 200 |
| `get_stock_events` | 热榜、龙虎榜、机构、涨跌停、停复牌、题材 | 每类200 |
| `get_market_rankings` | 市场热榜、涨跌停、龙虎榜、板块资金、题材排名 | 200 |
| `get_topic` | 概念、主题指数、每日题材及成分 | 500成分 |
| `get_index_or_etf` | 指数/ETF快照和历史、指数成分权重 | 1000 |
| `get_data_status` | 数据集最近发布版本与业务日期 | 50数据集 |

所有工具返回统一结构：`ok`、`data`、`meta`、`error`。金额和高精度数值以十进制
字符串返回，日期使用 ISO 8601。未指定交易日时，根据该工具实际依赖的数据集，从
`dataset_release` 选择最新共同发布日期；不会使用表中 `max(trade_date)` 猜测完整性。

涉及数值的工具在各自 `meta.units` 中按字段或字段组说明单位；股票和 ETF 价格是人民币
元，指数与板块 OHLC 是指数点位，不做全局推断。选股字段目录还会逐字段返回 `unit`。
`limit_list_d` 对应的 `*_raw` 字段因供应方未确认单位，会在响应中标为 `unknown`，不得
与统一人民币元字段聚合或比较；龙虎榜保留供应方金额口径，也不会被声明为统一人民币元。
前复权和后复权历史中的 `change` 使用对应复权收盘价计算；其中前复权是 Tushare 历史
当日快照口径，不会随当前日期动态重算。

题材名称先做代码/完整名称精确匹配；模糊匹配出现多个候选时返回
`AMBIGUOUS_IDENTIFIER`，不会随机选择。概念和主题指数成分表仅保存最近一次当前快照：
默认返回 `current_snapshot` 及 `member_observed_at`；指定历史交易日时只返回历史板块行情，
成员列表为空并标记 `unavailable_for_historical_date`，不会伪造历史成分。选股最多 20 个
条件、单个 `IN` 100 项、5 个排序字段和 50 个返回字段，避免生成无界 SQL。

## 强制边界

MCP 数据库访问统一使用 `app.mcp.mcp_read_only_query`。该入口只向查询仓储暴露
`McpReadOnlyQuery.execute(Select)`，不暴露 `AsyncSession`。同时实施：

1. 只接受 SQLAlchemy `SELECT`，递归拒绝原始 SQL、DML CTE 和行锁。
2. 禁止 `commit`、裸连接、`run_sync`、批量写入和包含变更的 `flush`。
3. PostgreSQL 连接设置 `default_transaction_read_only=on`。
4. MCP 启动时验证角色属性、成员关系、表/序列/函数权限和全部可读表。
5. 工具调用结束时统一回滚事务。
6. 未配置独立的 `MCP_DATABASE_URL` 或权限自检失败时拒绝启动。

普通 FastAPI 和 Scheduler 会话不受影响。

## 数据库角色

生产环境应由数据库管理员创建独立登录角色。密码通过安全方式生成并写入生产配置，禁止提交到仓库。

```sql
CREATE ROLE stock_mcp_reader
    LOGIN
    PASSWORD '<generated-password>'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT
    NOREPLICATION
    NOBYPASSRLS;
ALTER ROLE stock_mcp_reader SET default_transaction_read_only = on;
ALTER ROLE stock_mcp_reader SET statement_timeout = '30s';
ALTER ROLE stock_mcp_reader SET search_path = pg_catalog, public;

REVOKE TEMPORARY ON DATABASE stock_data_sync FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE stock_sync IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

GRANT CONNECT ON DATABASE stock_data_sync TO stock_mcp_reader;
GRANT USAGE ON SCHEMA public TO stock_mcp_reader;
GRANT SELECT ON TABLE
    trade_calendar,
    stock,
    stock_company,
    stock_daily,
    stock_technical_daily,
    stock_moneyflow_daily,
    ths_board_moneyflow_daily,
    stock_suspend_daily,
    concept_board,
    concept_board_daily,
    concept_board_member,
    theme_index,
    theme_index_daily,
    theme_index_member,
    stock_hot_rank_daily,
    market_theme_daily,
    market_theme_member_daily,
    stock_top_list_daily,
    stock_top_inst_daily,
    stock_limit_event_daily,
    stock_limit_step_daily,
    market_index,
    market_index_daily,
    index_daily_basic,
    market_index_weight,
    etf,
    etf_daily,
    etf_share_size_daily,
    dataset_release
TO stock_mcp_reader;
```

不要让该角色加入其他角色，也不要授予运行控制表、原始资产表、序列、函数、临时表、数据库或 schema 创建权限。新增业务表后必须显式补充 `SELECT` 授权，否则 MCP 启动自检会失败。

查询构造层还会拒绝 `literal_column`、原始 SQL 和未列入允许清单的 SQL 函数，避免通过合法 `SELECT` 调用 advisory lock、notify 或配置修改函数。字段和函数表达式必须由服务端目录生成，不能直接使用 MCP 参数拼装。

配置文件增加：

```dotenv
MCP_DATABASE_URL=postgresql+psycopg://stock_mcp_reader:<password>@<host>:<port>/stock_data_sync
MCP_QUERY_TIMEOUT_SECONDS=30
```

## 生产配置与启动

首次安装会自动生成独立密码、创建 `stock_mcp_reader` 并执行权限自检。已有安装升级后，
由管理员显式执行一次：

```bash
sudo stock-data-sync mcp setup
```

命令会补齐缺失的 MCP 配置、创建或收敛只读角色权限，然后用只读账号执行完整自检。
它不启动常驻进程。MCP 客户端每次需要时直接拉起稳定 stdio 入口：

```text
/Users/lingfeng/personal_apps/stock_data_sync/bin/stock-data-mcp
```

启动入口使用普通用户执行，不要添加 `sudo`。也可以不使用管理员权限查询该入口：

```bash
stock-data-sync mcp command
```

只有创建或收敛数据库只读角色的 `stock-data-sync mcp setup` 需要 `sudo`。

客户端配置示例：

```json
{
  "mcpServers": {
    "stock-data": {
      "command": "/Users/lingfeng/personal_apps/stock_data_sync/bin/stock-data-mcp"
    }
  }
}
```

开发环境使用 `make mcp-dev`，前提是本机 `.env` 已配置独立只读账号。MCP 进程启动时
都会重新验证数据库权限；任何越权、缺表授权或账号配置错误都会拒绝启动。写入数据库的
连接地址和密码在进入 MCP 进程前会从环境变量中移除。
