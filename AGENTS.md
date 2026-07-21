# 项目操作规则

- 远程部署机器统一使用 SSH 别名 `lingfeng-local` 连接，不直接使用 IP 地址或其他用户名。
- 远程 Web 入口固定为 `http://192.168.2.140/`；服务 API 监听 `192.168.2.140:8888`，不得改用 `127.0.0.1` 猜测监听地址。
- 远程主程序目录：`/Users/lingfeng/personal_apps/stock_data_sync`。
- 远程数据目录：`/Volumes/disk1/apps/stock_data_sync`。
- 排障默认只执行只读检查；修改配置、数据、服务状态或部署版本前，必须获得用户明确授权。
