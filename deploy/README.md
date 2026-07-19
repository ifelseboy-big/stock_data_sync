# 部署配置

- `production/install.sh`：GitHub Release 发布的一行安装入口。
- `production/install-local.sh`：拉取正式标签后执行的首次本地构建和初始化。
- `production/lib/deploy-common.sh`：本地构建、版本切换和安装发现公共逻辑。
- `production/bootstrap/`：不随版本变化的全局命令和 launchd 薄入口。
- `production/bin/stock-data-sync`：版本化服务管理、doctor、升级和程序回滚命令。
- `production/bin/run-service`：当前版本的实际进程入口。
- `../scripts/build-release.sh`：生成 GitHub Release 的安装器、manifest 和校验文件，不生成应用二进制包。

首次安装目录、Web/API 监听 IPv4、Web/API 端口和 PostgreSQL 端口都没有默认值，必须由用户明确指定；后续通过 `~/.stock-data-sync/install.conf` 自动发现。普通升级只更换程序，不备份或迁移数据库。完整流程见 [Mac mini 安装、升级与运行](../docs/06-deployment.md)。

PostgreSQL、Server、Scheduler 以及可选 Backup 会注册为系统级 launchd 服务，并显式执行 `launchctl enable`；前三者使用 `RunAtLoad` 和 `KeepAlive` 实现开机启动与异常退出自动拉起。
