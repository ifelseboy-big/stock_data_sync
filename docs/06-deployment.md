# Mac mini 发布与部署

生产环境是单台 Mac mini。PostgreSQL、FastAPI/Web 和 Scheduler 直接作为 macOS 进程运行，由 `launchd` 管理开机启动、异常拉起和进程生命周期。

这里的“原生进程”仅表示程序直接运行在 macOS 上。用户只面对一个统一安装目录和一套 `start`、`stop`、`restart`、`status`、`logs` 命令。

## 1. 部署不变量

安装目录必须由用户首次安装时明确指定，不提供默认值。输入为空时安装立即终止。应用、Python 环境、PostgreSQL 数据、Parquet 原始资产、日志、备份和配置全部位于该目录。

安装器不能自行选择 `/Applications`、`/opt`、用户主目录或其他路径。Homebrew 只提供 PostgreSQL 和 uv 的可执行文件，不使用 Homebrew 默认数据库目录，也不通过 `brew services` 管理生产数据库。

## 2. Mac mini 要求

- macOS，Apple Silicon 与 Intel 均可。
- 管理员权限，首次安装和服务管理命令使用 `sudo`。
- Homebrew。
- PostgreSQL 16：`brew install postgresql@16`。
- uv：`brew install uv`。
- 首次安装 Python 依赖时能够访问 Python 包源。

Node.js 只在生成发布包的开发机器上使用。发布包已经包含 Vue 构建产物，Mac mini 运行时不需要 Node.js 或 Nginx。

如果执行过 `brew services start postgresql@16`，应先停止，或者安装时通过 `--postgres-port` 选择未占用端口。本项目使用独立数据库目录和独立 launchd 服务，不能与 Homebrew 默认实例共用同一个数据目录。

## 3. 生成发布包

在开发机器的项目根目录执行：

```bash
make release VERSION=0.1.0
```

发布脚本先执行 Vue 类型检查和生产构建，再生成：

```text
dist/stock-data-sync-0.1.0.tar.gz
dist/stock-data-sync-0.1.0.tar.gz.sha256
```

将两个文件传到 Mac mini，校验并解压：

```bash
shasum -a 256 -c stock-data-sync-0.1.0.tar.gz.sha256
tar -xzf stock-data-sync-0.1.0.tar.gz
cd stock-data-sync-0.1.0
```

## 4. 首次安装

交互安装：

```bash
sudo ./deploy/production/install.sh
```

安装器要求输入安装目录和 Web/API 端口，两项均不能为空。服务用户默认采用发起 `sudo` 的 macOS 用户。

非交互安装：

```bash
sudo ./deploy/production/install.sh \
  --install-dir /Users/stockops/stock-data-sync \
  --http-port 8080
```

示例路径仅表示参数格式，不是默认安装目录。可选参数：

```text
--http-bind ADDRESS    Web/API 监听地址，默认 0.0.0.0
--postgres-port PORT   PostgreSQL 本机端口，默认 5432
--service-user USER    实际运行服务的 macOS 用户
--no-start             安装完成后暂不启动
```

安装过程会：

1. 校验 macOS、Homebrew PostgreSQL 16、uv、端口及空安装目录。
2. 复制服务端和已构建的 Web 文件。
3. 使用 uv 在安装目录内创建 Python 虚拟环境。
4. 在安装目录初始化独立 PostgreSQL 16 数据目录和随机密码。
5. 生成权限为 `0600` 的运行配置。
6. 生成并注册 PostgreSQL、API/Web、Scheduler 三个 launchd 服务。
7. 启动 PostgreSQL、创建数据库、执行 Alembic 迁移，再启动应用服务。

## 5. 安装目录

假设用户明确指定 `/Users/stockops/stock-data-sync`：

```text
/Users/stockops/stock-data-sync/
├── app/
│   ├── server/               # FastAPI、Scheduler 及安装目录内的 .venv
│   └── web/                  # Vue 生产构建文件
├── backups/                  # 数据库及原始资产备份暂存
├── bin/
│   ├── run-service           # launchd 使用的内部进程入口
│   └── stock-data-sync       # 统一服务管理命令
├── config/
│   ├── app.env               # 密钥和运行配置，权限 0600
│   └── launchd/              # 当前安装对应的 launchd 配置副本
├── data/
│   ├── postgres/             # 独立 PostgreSQL 16 数据目录
│   └── raw/                  # 不可变 Parquet 原始资产
└── logs/
    ├── postgres/
    ├── scheduler/
    └── server/
```

launchd 注册时会把三个 plist 复制到 `/Library/LaunchDaemons`。这里仅保存系统服务注册信息；所有业务持久数据仍在用户指定目录内。

## 6. 进程模型

| 服务 | 运行方式 | 网络与数据边界 |
| --- | --- | --- |
| PostgreSQL 16 | Homebrew 可执行文件 + 自有数据目录 | 只监听 `127.0.0.1`，不对外暴露 |
| API/Web | 安装目录 `.venv` 中的 Uvicorn | 同一端口提供 Vue、`/api/v1`、健康检查和指标 |
| Scheduler | 安装目录 `.venv` 中的独立 Python 进程 | 读写 PostgreSQL 和 `data/raw`，统一调用 Tushare |

API 与 Scheduler 使用相同代码版本但不同进程。只有 Scheduler 读写原始资产；浏览器不能直接访问文件目录。launchd 设置 `RunAtLoad` 和 `KeepAlive`，Mac mini 重启后自动恢复服务。

## 7. 服务管理

先把变量设为用户实际选择的安装目录：

```bash
INSTALL_DIR=/Users/stockops/stock-data-sync
sudo "$INSTALL_DIR/bin/stock-data-sync" start
sudo "$INSTALL_DIR/bin/stock-data-sync" stop
sudo "$INSTALL_DIR/bin/stock-data-sync" restart
sudo "$INSTALL_DIR/bin/stock-data-sync" status
sudo "$INSTALL_DIR/bin/stock-data-sync" logs server
```

可以操作指定服务：

```bash
sudo "$INSTALL_DIR/bin/stock-data-sync" restart server
sudo "$INSTALL_DIR/bin/stock-data-sync" restart scheduler
sudo "$INSTALL_DIR/bin/stock-data-sync" logs postgres
sudo "$INSTALL_DIR/bin/stock-data-sync" doctor
sudo "$INSTALL_DIR/bin/stock-data-sync" migrate
```

Web 由 `server` 提供，因此 `web` 是 `server` 的别名，不存在第四个长期进程。`stop` 只卸载 launchd 服务，不删除数据库、原始资产或日志。

## 8. 修改配置

使用管理员权限编辑 `$INSTALL_DIR/config/app.env`，然后重启受影响服务。配置文件由 root 持有、权限为 `0600`，并通过只读 ACL 授权服务用户读取。首次安装时允许暂不填写 Tushare Token；后续设置 `TUSHARE_TOKEN` 并执行：

```bash
sudo "$INSTALL_DIR/bin/stock-data-sync" restart server scheduler
```

配置文件是可执行的受限 Shell 环境文件，只能由服务用户和管理员读取。不得把 Token、数据库密码或完整配置输出到日志。

若只允许 Mac mini 本机访问，将 `HTTP_BIND` 改为 `127.0.0.1`。局域网访问使用 `0.0.0.0`，同时应通过 macOS 防火墙限制来源；公网访问必须增加受信任 TLS 反向代理和管理员鉴权。

## 9. 备份与恢复

| 备份项 | 频率 | 保留规则 | 恢复验证 |
| --- | --- | --- | --- |
| PostgreSQL 自定义格式逻辑备份 | 每日一次 | 7 个日备、4 个周备、12 个月备 | 每月在隔离库恢复并核对行数 |
| Parquet 原始资产 | 每日增量 | 与生产资产同生命周期 | 抽样核对 SHA-256 并重放一个数据集 |
| 配置和监控规则 | 每次发布 | 跟随发布版本，密钥只保存加密副本 | 新环境从发布包和密钥恢复 |

备份清单必须包含 SHA-256 并复制到 Mac mini 之外的存储；只保存在同一磁盘不能视为有效备份。单机部署目标为 RPO 不超过 24 小时、RTO 不超过 4 小时。

恢复顺序为 PostgreSQL、Parquet 原始资产、数据库迁移校验、只读 API、Scheduler、Web。Scheduler 启动后必须先执行状态协调，不能立即无界追赶历史任务。

## 10. 故障恢复

| 故障场景 | 系统行为 | 恢复依据 |
| --- | --- | --- |
| Scheduler 进程退出 | advisory lock 自动释放；重启后先协调再恢复派发 | 数据库任务状态、封存资产和 `dataset_release` |
| 采集运行中崩溃 | 核对临时文件、最终文件、资产记录和任务状态 | `task_id` 唯一资产和最终文件 |
| 加工运行中崩溃 | 已发布则补记成功；未发布则由 PostgreSQL 回滚后重新排队 | `dataset_release`、事务原子性和加工锁 |
| 数据库暂不可用 | 停止领取新任务，不在内存中宣称任务完成 | 数据库恢复后重试 |
| Parquet 磁盘写满 | 当前采集失败且不创建资产，触发容量反压 | 临时目录、任务错误和磁盘监控 |
| Tushare 数据迟到或为空 | 截止时间前退避重试；原批次关闭后创建 REPAIR 批次 | `ApiSpec.empty_policy` 和业务发布时间 |
| 供应方修订历史数据 | 创建新资产和输出版本，只重算受影响范围 | 资产哈希、处理器版本和依赖图 |
| 分区缺失 | 加工任务进入 BLOCKED 并告警 | 预建分区配置和状态协调器 |

启动恢复顺序固定为：取得调度单例锁、检查迁移版本和分区、清理超过 24 小时的临时原始文件、核对 RUNNING 采集与加工任务、重新计算可关闭批次、补建已关闭批次的加工计划，最后才恢复任务领取。

自动补采只处理可恢复失败，并受次数和截止时间限制。原批次关闭前在原任务上重试；关闭后新建 REPAIR 批次。修复成功后只解除受影响依赖，不重跑无关数据集。

## 11. 安全与权限

本期按内部单管理员系统设计。Tushare Token、管理 Token 和数据库密码不得写入数据库、日志或前端响应。对公网开放前必须完成 TLS、访问控制和管理员鉴权。

| 角色或配置 | 权限与规则 |
| --- | --- |
| 数据库迁移账号 | 拥有 DDL 和对象所有权，只在迁移时使用 |
| 应用读写账号 | API 和 Scheduler 使用，只允许所需 DML、序列和函数 |
| 研究只读账号 | 只读正式业务表和 `dataset_release` |
| `TUSHARE_TOKEN` | 只存在权限 `0600` 的 `app.env` 或外部密钥系统 |
| `ADMIN_API_TOKEN` | 与 Tushare Token 分离并支持轮换，只保护管理写接口 |
| `DATABASE_URL` | 日志只显示数据库主机和库名，不输出完整值 |

PostgreSQL 只监听本机回环地址。API/Web 根据安装配置绑定本机或局域网地址；管理写接口不允许跨域通配。服务启动时必须校验 Tushare 请求预算、时区、加工并发数、原始目录权限、分区和数据库迁移版本，关键配置无效时直接退出。

研究消费者不能读取 `collection_task.request_params` 和 `raw_data_asset.storage_uri`。取消、跳过、重试和回填必须提交原因并记录操作者与 `request_id`。删除原始资产、删除分区、覆盖备份和回滚迁移不通过普通 Web API 提供。
