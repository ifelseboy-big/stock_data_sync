# 部署配置

- `production/install.sh`：Mac mini 首次安装入口。
- `production/bin/run-service`：`launchd` 使用的内部进程入口。
- `production/bin/stock-data-sync`：统一的服务管理、检查、迁移、备份、校验和恢复命令。
- `../scripts/build-release.sh`：生成包含服务端源码、Vue 构建产物和安装器的发布包。

安装目录不在代码中预设，由用户首次安装时明确指定。有效备份目标同样由用户指定，并且必须位于安装目录之外。完整流程见 [Mac mini 发布与部署](../docs/06-deployment.md)。
