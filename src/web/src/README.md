# 前端分层规范

```text
src/
├── api/          # Axios 实例、拦截器和跨模块 HTTP 基础设施
├── components/   # 无业务归属的通用组件
├── layouts/      # 页面框架
├── modules/      # 按业务能力组织页面、API、类型和状态
├── plugins/      # 第三方库初始化
├── router/       # 路由装配与路由守卫
├── stores/       # 全局 UI、用户和权限状态
├── styles/       # 全局设计变量和基础样式
├── types/        # 跨模块 TypeScript 类型
└── utils/        # 无状态纯函数
```

模块内优先使用 `api.ts`、`types.ts`、`store.ts`、`views/`、`components/`，
禁止页面组件直接使用 Axios，也不要把页面私有状态放进全局 Store。
