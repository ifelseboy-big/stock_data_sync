# Stock Data Sync

股票数据同步与运维管理平台工程骨架。

## 技术栈

- 后端：Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL、APScheduler、Tushare、uv
- 前端：Vue 3、TypeScript、Vite、Element Plus、Pinia、Vue Router、ECharts

## 目录

```text
.
├── src/
│   ├── server/              # Python 后端服务
│   └── web/                 # Vue 3 管理端
├── deploy/
│   └── production/          # Mac mini 安装与 launchd 服务管理
├── docs/                    # 系统设计与数据模型
├── .env.example             # 环境变量模板
├── Makefile                 # 常用开发命令入口
└── README.md                # 项目说明
```

业务源码统一放在 `src`。部署文件放在 `deploy`，文档放在 `docs`，根目录只保留整个项目共用的配置和入口文件。

- [文档中心与阅读顺序](docs/README.md)
- [系统架构与工程分层](docs/01-system-architecture.md)
- [任务调度与数据加工流程](docs/02-task-workflow.md)
- [Tushare 采集设计](docs/03-tushare-collection.md)
- [管理后台与可观测性](docs/04-admin-console.md)
- [数据模型](docs/data-model/README.md)
- [Mac mini 发布与部署](docs/06-deployment.md)

## 本地启动

本地开发需要可访问的 PostgreSQL 18。Mac 上安装并启动：

```bash
brew install postgresql@18
export PATH="$(brew --prefix postgresql@18)/bin:$PATH"
brew services start postgresql@18
psql postgres -c "CREATE ROLE stock_sync LOGIN PASSWORD 'stock_sync';"
createdb --owner=stock_sync stock_data_sync
```

然后启动应用：

```bash
cp .env.example .env
make server-install
make server-dev
```

另开终端启动前端：

```bash
make web-install
make web-dev
```

需要执行定时任务时，再启动独立调度进程：

```bash
make scheduler-dev
```

`Makefile` 只是开发命令快捷入口，不参与程序运行；不使用 `make` 也可以直接执行对应命令。

- 管理端：http://localhost:5173
- API：http://localhost:8000
- API 文档：http://localhost:8000/docs
- Prometheus 指标：http://localhost:8000/metrics

## 数据库迁移

```bash
cd src/server
uv run alembic revision --autogenerate -m "add tables"
uv run alembic upgrade head
```

## 发布与安装

```bash
git tag v1.2.3
git push origin v1.2.3
```

GitHub Actions 会校验代码并生成 Release 安装入口。目标 Mac 从正式标签拉取源码并在本地构建。首次安装必须明确指定主程序目录和数据目录：

```bash
curl -fsSL https://github.com/ORG/stock-data-sync/releases/latest/download/install.sh \
  | sudo bash -s -- \
      --program-dir /系统盘/用户指定的程序目录 \
      --data-dir /外接硬盘/用户指定的数据目录 \
      --http-bind 0.0.0.0 \
      --http-port 18080 \
      --postgres-port 15432
```

主程序目录所在磁盘必须启用 ownership；数据目录可以位于关闭 ownership 的外接盘。以后升级不需要再次指定目录：

```bash
sudo stock-data-sync upgrade
```

普通升级只切换程序，不备份或迁移数据库。完整说明见 [Mac mini 安装、升级与运行](docs/06-deployment.md)。
