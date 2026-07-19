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
│   ├── docker/              # 应用镜像配置
│   ├── local/               # 本地 PostgreSQL
│   └── production/          # 生产安装与服务管理
├── docs/                    # 架构、依赖和请求策略文档
├── .env.example             # 环境变量模板
├── Makefile                 # 常用开发命令入口
└── README.md                # 项目说明
```

业务源码统一放在 `src`。部署文件放在 `deploy`，文档放在 `docs`，根目录只保留整个项目共用的配置和入口文件。

- [工程分层](docs/architecture.md)
- [核心依赖](docs/dependencies.md)
- [Tushare 请求策略](docs/request-strategy.md)
- [任务处理需求](docs/task-processing-requirements.md)
- [管理后台设计与指标口径](docs/admin-console-design.md)
- [发布与部署](docs/deployment.md)

## 本地启动

```bash
cp .env.example .env
make db-up
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

`Makefile` 只是命令快捷入口，不参与程序运行。例如 `make db-up` 实际执行的是：

```bash
docker compose --env-file .env -f deploy/local/compose.yaml up -d postgres
```

不使用 `make` 也可以直接执行对应命令。

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

## 发布

```bash
make release VERSION=0.1.0
```

生产服务器上的安装目录由用户首次安装时指定，没有内置默认目录。安装及 `start`、`stop`、`restart` 命令见[发布与部署](docs/deployment.md)。
