# data - AI Agents 局部约定

> 继承根 `AGENTS.md`；本文件约束 SQLite、仓储与导出实现。

- `Database` 以 `PRAGMA user_version` 管理 schema；当前版本为 1，拒绝直接打开更高版本，后续 schema 变更必须提供显式迁移和测试。
- `SessionRepository.save_session()` 在单个事务中写入会话、可选的一对一校准快照及全部单次结果；`session_uid` 和 `(session_id, sequence)` 唯一约束用于防重复保存。
- 数据模型保留原始/补偿延迟、质量与时间映射字段、无效原因及 float32 波形；本层不依赖 core，统计重算由 `core.persistence_service` 完成。
- CSV 使用 UTF-8 BOM 并按单次结果逐行导出；Excel 分为“会话”和“单次测量”工作表。两种导出都必须保留模拟标记、无效原因和追溯字段。
- 修改 schema、仓储或导出后验证 `tests/test_persistence.py`；架构专题 @see docs/ai/04_ARCHITECTURE.md、docs/ai/01_CONVENTIONS.md。
