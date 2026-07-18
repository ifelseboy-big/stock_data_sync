# 模块分层规范

每个业务模块内部遵循单向依赖：

```text
api → service → repository → models
       ↓
   integrations（通过协议接口访问）
```

- `api.py`：HTTP 参数、权限、状态码和响应，不写业务规则。
- `schemas.py`：Pydantic 输入输出模型，不作为数据库实体。
- `service.py`：业务用例和事务编排，不依赖 FastAPI。
- `repository.py`：SQLAlchemy 查询和持久化细节。
- `models.py`：SQLAlchemy 数据模型，只表达存储结构和约束。

模块之间优先通过 service 或显式协议协作，禁止跨模块直接查询对方的数据表。

