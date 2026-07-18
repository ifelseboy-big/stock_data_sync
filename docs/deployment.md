# 单机发布与部署

当前部署方案面向一台 Linux 服务器，使用 Docker Compose 管理 PostgreSQL、后端 API、任务调度器和 Web 管理端，不依赖 Redis。

## 1. 服务器要求

- Linux 服务器
- Docker Engine
- Docker Compose v2
- `sudo`、`openssl`、`tar`
- 首次构建时能够访问容器镜像仓库、PyPI 和 npm registry

安装脚本必须以 root 权限运行，因为需要给 PostgreSQL 和应用日志目录设置容器用户权限。

## 2. 生成发布包

在项目根目录执行：

```bash
make release VERSION=0.1.0
```

生成：

```text
dist/stock-data-sync-0.1.0.tar.gz
dist/stock-data-sync-0.1.0.tar.gz.sha256
```

将这两个文件上传到服务器，校验并解压：

```bash
sha256sum -c stock-data-sync-0.1.0.tar.gz.sha256
tar -xzf stock-data-sync-0.1.0.tar.gz
cd stock-data-sync-0.1.0
```

## 3. 首次安装

交互安装：

```bash
sudo ./deploy/production/install.sh
```

安装器会要求输入安装目录和 Web 端口，两项都没有默认值。安装目录必须是空目录或尚未创建的绝对路径；输入为空会终止安装。

也可以明确传参，适合自动化部署：

```bash
sudo ./deploy/production/install.sh \
  --install-dir /data/apps/stock-data-sync \
  --http-port 8080
```

安装器随后会：创建统一目录结构，生成数据库密码，复制应用，构建镜像，执行数据库迁移并启动服务。使用 `--no-start` 可以只完成安装和镜像构建，不启动服务。

## 4. 安装目录

假设用户指定 `/data/apps/stock-data-sync`：

```text
/data/apps/stock-data-sync/
├── app/                    # 当前发布版本的构建源文件
├── backups/                # 预留数据库备份目录
├── bin/
│   └── stock-data-sync     # 服务管理命令
├── config/
│   └── app.env             # 运行配置及密钥，权限 0600
├── data/
│   └── postgres/           # PostgreSQL 数据文件
├── logs/
│   ├── nginx/
│   ├── postgres/
│   ├── scheduler/
│   └── server/
└── compose.yaml
```

数据库、日志和配置都在用户指定的安装目录内。Docker 不创建 Redis 服务，也不把 PostgreSQL 暴露到宿主机端口。

## 5. 服务管理

先将变量设为用户实际选择的目录：

```bash
INSTALL_DIR=/data/apps/stock-data-sync
"$INSTALL_DIR/bin/stock-data-sync" start
"$INSTALL_DIR/bin/stock-data-sync" stop
"$INSTALL_DIR/bin/stock-data-sync" restart
"$INSTALL_DIR/bin/stock-data-sync" status
"$INSTALL_DIR/bin/stock-data-sync" logs
```

可以只操作指定服务：

```bash
"$INSTALL_DIR/bin/stock-data-sync" restart server
"$INSTALL_DIR/bin/stock-data-sync" restart scheduler
"$INSTALL_DIR/bin/stock-data-sync" logs postgres
```

`stop` 只停止容器，不删除数据库和日志。全部启动或重启时，会先检查 PostgreSQL并执行数据库迁移。各长期运行服务使用 `restart: unless-stopped`，服务器重启后会随 Docker 自动恢复；被人工 `stop` 的服务需要再次执行 `start`。

## 6. 修改配置

编辑 `$INSTALL_DIR/config/app.env`。首次安装时可以暂不填写 Tushare Token，之后设置 `TUSHARE_TOKEN` 并执行：

```bash
"$INSTALL_DIR/bin/stock-data-sync" restart server scheduler
```

Web 默认监听所有网卡。若只允许本机访问，将 `HTTP_BIND` 改为 `127.0.0.1` 后重启 `web`。
