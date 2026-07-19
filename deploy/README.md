# 部署配置

- `production/install.sh`：Mac mini 首次安装入口。
- `production/bin/run-service`：`launchd` 使用的内部进程入口。
- `production/bin/stock-data-sync`：统一的启动、停止、重启、状态和日志命令。
- `../scripts/build-release.sh`：生成包含服务端源码、Vue 构建产物和安装器的发布包。

安装目录不在代码中预设，由用户首次安装时明确指定。完整流程见 [Mac mini 发布与部署](../docs/06-deployment.md)。
