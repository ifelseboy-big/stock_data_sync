# 部署配置

部署相关文件与业务源码分开存放：

- `local/compose.yaml`：仅用于本地启动 PostgreSQL。
- `docker/`：后端、前端镜像和 Nginx 配置。
- `production/`：单机安装器、生产 Compose 配置和服务管理命令。

生产安装目录不在代码中预设，由用户首次安装时明确指定。完整流程见 [`docs/deployment.md`](../docs/deployment.md)。
