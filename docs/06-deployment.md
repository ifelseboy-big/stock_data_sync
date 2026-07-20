# Mac mini 安装、升级与运行

生产环境是单台 Mac。PostgreSQL、FastAPI/Web 和 Scheduler 作为原生 macOS 进程运行，由 `launchd` 管理。程序源码来自 GitHub 正式版本标签，并在目标 Mac 本地构建。

## 1. 基本原则

- 首次安装的主程序目录、数据目录、Web/API 监听 IPv4、Web/API 端口和 PostgreSQL 端口都没有默认值，必须由用户明确传入。
- 安装成功后，两个目录记录在服务用户的 `~/.stock-data-sync/install.conf`；后续升级不再要求目录。
- Git 只拉取不可变的 `vX.Y.Z` 正式标签，不部署 `main`。
- 每个版本独立构建，使用 `current` 软链接原子切换。
- 源码、构建版本和配置位于主程序目录；PostgreSQL、Parquet、日志和备份位于数据目录，均不随版本切换。
- 普通升级只更换程序，不备份、不恢复、不迁移数据库。
- 目标程序需要不同数据库 revision 时，doctor 在停止服务前拒绝普通升级。
- 首次初始化空数据库时允许执行 Alembic 初始迁移。

## 2. 目标 Mac 要求

- macOS，Apple Silicon 或 Intel。
- 管理员权限。
- Homebrew。
- Git。
- Node.js 22 或更高版本，以及 npm 10+。安装器会选择机器上可用的最高 Node.js 版本。
- uv。
- PostgreSQL 18。
- 能访问 GitHub、npm 和 Python 包源。
- 数据目录可以位于关闭 ownership 的外接磁盘。

安装依赖：

```bash
brew install node uv postgresql@18
```

首次安装时应为项目选择一个未占用的 `--postgres-port`。项目使用独立 PostgreSQL 数据目录，不使用 Homebrew 默认数据目录；doctor 会拒绝使用已被其他 PostgreSQL 实例占用的端口。
PostgreSQL 服务启动时固定使用 `shared_buffers=1GB`，覆盖 `initdb` 生成的初始值。
`initdb` 过程中仍可能显示默认值 `128MB`；服务启动后安装器会输出实际生效值，
`status` 和 `doctor` 也会读取运行中的数据库进行展示与校验。
launchd 服务显式使用目标 Mac 已支持的 `C.UTF-8` locale，避免 PostgreSQL 18 在 macOS 后台启动时触发多线程初始化保护。

## 3. GitHub 发布

正式发布使用语义化版本标签：

```bash
git tag v1.2.3
git push origin v1.2.3
```

`.github/workflows/release.yml` 会在干净的 macOS runner 上执行后端和前端检查，然后创建 GitHub Release。Release 只发布小型安装入口和版本元数据：

```text
install.sh
release-manifest.json
SHA256SUMS
```

应用本身不在 GitHub Actions 中预编译。目标 Mac 会从标签拉取源码，再执行 `npm ci`、`npm run build` 和 `uv sync --frozen --no-dev`。

仓库应在 GitHub 设置中启用 immutable releases。安装器资产会内置当前 Release 的仓库地址和版本号。

## 4. 首次安装

首次安装必须明确指定主程序目录和数据目录：

```bash
curl --proto '=https' --tlsv1.2 -fsSL \
  https://github.com/ORG/stock-data-sync/releases/latest/download/install.sh \
  | sudo bash -s -- \
      --program-dir /Users/stockops/apps/stock-data-sync \
      --data-dir /Volumes/disk1/apps/stock_data_sync \
      --http-bind 0.0.0.0 \
      --http-port 18080 \
      --postgres-port 15432
```

缺少 `--program-dir`、`--data-dir`、`--http-bind`、`--http-port` 或 `--postgres-port` 时安装立即终止。两个目录必须是相互独立的绝对路径且为空。数据目录允许关闭 ownership。安装器创建主程序目录后会验证它能够由 root 持有，doctor 还会检查端口冲突和两个磁盘的可用空间。

首次安装必填参数：

```text
--program-dir PATH      主程序、源码和配置目录
--data-dir PATH         PostgreSQL、行情数据、日志和备份目录
--http-bind IPv4        Web/API 监听 IPv4，例如 127.0.0.1 或 0.0.0.0
--http-port PORT        Web/API 监听端口
--postgres-port PORT    PostgreSQL 本机监听端口
```

常用可选参数：

```text
--version VERSION       安装指定版本
--service-user USER     服务用户，默认使用发起 sudo 的用户
--backup-dir PATH       可选的数据目录外备份目标
--no-start              初始化完成后暂不启动 Server 和 Scheduler
```

安装过程固定执行：

1. `pre-install doctor` 检查系统、用户、双目录、依赖、端口和服务冲突。
2. 克隆正式 Git 标签到本地源码镜像。
3. 在独立版本目录执行前端和 Python 构建。
4. 生成带逐项注释的 `config/app.env`。
5. 初始化独立 PostgreSQL 18 数据目录和空数据库。
6. 对空数据库执行 Alembic 初始迁移。
7. 注册并显式启用 launchd，启动服务。
8. `post-install doctor` 检查版本、配置、数据库、进程和 HTTP 健康接口。

任何安装前检查失败都不会开始安装。

## 5. 目录布局

假设主程序目录选择 `/Users/stockops/apps/stock-data-sync`：

```text
/Users/stockops/apps/stock-data-sync/
├── source/
│   └── repository.git/       # Git bare mirror
├── releases/
│   ├── 1.2.2-<commit>/
│   └── 1.2.3-<commit>/
├── current -> releases/1.2.3-<commit>
├── previous -> releases/1.2.2-<commit>
├── bin/                      # 不随版本变化的薄入口
├── logs/launchd/             # launchd 启动阶段日志
└── config/
    ├── app.env               # 唯一运行配置文件
    └── launchd/
```

数据目录可以位于关闭 ownership 的外接磁盘：

```text
/Volumes/disk1/apps/stock_data_sync/
├── data/
│   ├── postgres/
│   └── raw/
├── logs/
└── backups/
```

launchd 直接执行 macOS 自带的 `/bin/bash`，并将系统盘上的 `/usr/local/libexec/stock-data-sync/run-service` 作为受信任脚本参数，再启动主程序目录中的当前版本。launchd 自身的 stdout/stderr 位于主程序目录，避免它在启动进程前访问关闭 ownership 的外接盘；PostgreSQL 和应用日志仍位于数据目录。

服务用户主目录另有：

```text
~/.stock-data-sync/install.conf
```

它只记录主程序目录、数据目录、Git 仓库、渠道和当前版本，不保存 Token、数据库密码或业务配置。全局 `/usr/local/bin/stock-data-sync` 通过该文件找到两个目录。

## 6. 配置

唯一配置文件为：

```text
<PROGRAM_DIR>/config/app.env
```

文件权限为 `0600`、root 持有，并通过只读 ACL 允许服务用户读取。配置使用注释分组，每个 Key 上方说明含义以及“用户可修改”或“安装器维护”。

查看、校验和编辑：

```bash
sudo stock-data-sync config path
sudo stock-data-sync config show
sudo stock-data-sync config validate
sudo stock-data-sync config edit
```

修改完成后按需重启：

```bash
sudo stock-data-sync restart
```

管理页面会从后端自动读取 `ADMIN_API_TOKEN` 并用于写操作，不要求用户在每个操作弹窗中输入。

升级不会覆盖用户已有值。新程序必须为新增可选配置提供代码默认值；新增必填配置时，目标版本的 doctor 必须在切换前失败并提示用户处理。

## 7. 普通升级

升级不需要再次指定主程序目录或数据目录：

```bash
sudo stock-data-sync upgrade
```

如果确认升级前 doctor 的失败项不影响本次更换程序，可显式忽略并继续；升级后的 doctor 仍必须通过：

```bash
sudo stock-data-sync upgrade --ignore-doctor
```

如果旧版本自身的 doctor 缺陷阻止普通升级，可由新版本安装器读取安装记录并接管升级：

```bash
curl -fsSL https://github.com/ORG/stock_data_sync/releases/latest/download/install.sh \
  | sudo bash -s -- --upgrade --ignore-doctor
```

指定版本：

```bash
sudo stock-data-sync upgrade --version 1.2.3
```

升级流程：

1. 获取升级锁并启动 PostgreSQL（如尚未运行）。
2. 执行 `pre-upgrade doctor`。
3. `git fetch --tags`，解析目标正式标签。
4. 在新的 release 目录本地构建。
5. 执行 `build doctor`，验证 Python、Web、配置和 Alembic head。
6. 比较生产数据库 revision 与目标程序要求；不一致立即终止。
7. 停止 Server 和 Scheduler。
8. 原子切换 `current`，启动新程序。
9. 执行 `post-upgrade doctor`。
10. 检查失败时自动切回旧程序并重新检查。

普通升级不会调用 `backup`、`restore` 或 `migrate`，PostgreSQL 和 Parquet 不会被修改。只有管理员显式提交 `upgrade --migrate --database-backup ...` 时，升级流程才会先备份数据库并执行目标版本迁移。

## 8. 回滚

回滚也只切换程序：

```bash
sudo stock-data-sync rollback
sudo stock-data-sync rollback --version 1.2.2
```

回滚前必须确认目标程序要求的数据库 revision 与当前生产数据库一致。数据库不兼容时回滚被拒绝。

## 9. Doctor

手工执行：

```bash
sudo stock-data-sync doctor
```

安装和升级会自动执行对应阶段的 doctor。输出分为：

```text
PASS  检查通过
WARN  非阻断问题
FAIL  阻断安装、升级或切换
```

检查内容包括配置权限与语法、程序构建完整性、版本/commit、Python 导入、Web 产物、PostgreSQL、数据库 revision、launchd、服务状态和 API 健康接口。日志不会输出 Token、密码或完整数据库连接地址。

## 10. 服务管理

```bash
sudo stock-data-sync start
sudo stock-data-sync stop
sudo stock-data-sync restart
sudo stock-data-sync status
sudo stock-data-sync version
sudo stock-data-sync logs server
sudo stock-data-sync logs scheduler
```

普通 `start` 和 `restart` 只检查数据库兼容性，不执行 Alembic 迁移。

## 11. 数据库结构升级

数据库 revision 变化不属于普通程序升级。当前只保留显式管理员入口：

```bash
sudo stock-data-sync migrate
```

该命令不会被 `start`、`restart` 或普通 `upgrade` 自动调用。涉及数据库结构时使用经过确认的 `upgrade --migrate --database-backup ...`；PostgreSQL 大版本升级仍需单独设计验证和恢复方案。

`v0.1.18` 的数据库变更必须依次执行 `20260720_0008` 和 `20260720_0009`：前者新增可恢复的延迟采集阶段表，后者为加工任务增加数据质量警告字段。该升级不删除、不覆盖已有业务数据。完成后使用目标版本环境确认 revision：

```bash
set -a
source /用户指定的主程序目录/config/app.env
set +a
cd /用户指定的主程序目录/current/server
.venv/bin/python -m alembic -c alembic.ini current
```

v0.1.18 预期输出包含：

```text
20260720_0009 (head)
```

v0.1.20 在此基础上新增 `20260720_0010`，为运行记录的恢复状态查询增加成功任务部分索引，不修改已有数据。迁移后的预期输出为：

```text
20260720_0010 (head)
```

v0.1.21 新增保留数据的 `20260720_0011`。正式升级使用一个命令完成数据库备份、停止 Server 和 Scheduler、执行目标版本迁移、切换程序、重启和健康检查：

```bash
sudo stock-data-sync upgrade \
  --version 0.1.21 \
  --migrate \
  --database-backup /用户指定的绝对目录/stock-data-sync-before-0.1.21.dump
```

备份文件不得已存在，也不得放进 PostgreSQL 数据目录；命令同时生成 `.sha256` 校验文件。迁移前会检查 `ths_board_moneyflow_daily` 的新主键冲突，发现冲突时事务回滚、原程序不切换、原数据不删除。迁移后预期输出为：

```text
20260720_0011 (head)
```

带 `--migrate` 的升级如果在迁移后健康检查失败，不会自动降级数据库，也不会把旧程序强行运行在新 revision 上；新程序目录和数据库备份会保留，Server 与 Scheduler 停止，等待人工诊断。

v0.1.22 修复同花顺热榜历史回填：当日任务继续使用 `is_new=Y`，BACKFILL/REPAIR 自动使用 `is_new=N` 并由加工层选择最新完整分钟快照。该版本不新增数据库迁移，目标 revision 仍为 `20260720_0011`，从 v0.1.21 升级不需要 `--migrate`：

```bash
sudo stock-data-sync upgrade --version 0.1.22
```

v0.1.23 修正 PostgreSQL 连接监控口径，并在 Scheduler 获取会话级单例锁后立即结束隐式事务，避免长期 `idle in transaction`。该版本不新增数据库迁移，目标 revision 仍为 `20260720_0011`：

```bash
sudo stock-data-sync upgrade --version 0.1.23
```

详细字段、约束和索引见[系统运行与发布表](data-model/02-runtime-tables.md)和[数据库落地设计](data-model/06-database-implementation.md)。

## 12. 手工备份与恢复

备份能力保留给管理员使用，但普通程序升级不会自动调用：

```bash
sudo stock-data-sync backup /Volumes/StockBackup/daily
sudo stock-data-sync backup /Volumes/StockBackup/full --full
sudo stock-data-sync verify-backup /Volumes/StockBackup/full/stock-data-sync-backup-...
```

恢复会替换数据库和原始资产，必须显式停止应用服务并提交确认：

```bash
sudo stock-data-sync stop server scheduler
sudo stock-data-sync restore /Volumes/StockBackup/full/stock-data-sync-backup-... \
  --confirm-database-replace
```

恢复不会自动执行数据库迁移。

## 13. 网络安全

PostgreSQL 只监听 `127.0.0.1`。HTTP 默认监听 `0.0.0.0` 供局域网访问；只允许本机访问时将 `HTTP_BIND` 改为 `127.0.0.1`。公网开放前必须增加 TLS、访问控制和防火墙限制。
