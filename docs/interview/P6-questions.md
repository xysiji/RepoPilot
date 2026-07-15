# P6 面试题：持久化、上下文与 Trace

## 一、概念题（1～5）

### 1. LangGraph checkpoint 与 RepoPilot `runs` 表分别是什么？

- 追问 1：为什么不能只查询 `runs.status`？
- 追问 2：为什么 RuntimeStore 不保存 messages？

### 2. `run_id` 与 `thread_id` 有什么区别？

- 追问 1：P6 为什么让两者当前取同一个值？
- 追问 2：重启时误用新 thread_id 会发生什么？

### 3. persistent context 与 transient model context 有什么区别？

- 追问 1：为什么不能直接裁掉 checkpoint 中的旧 messages？
- 追问 2：`latest_context_stats` 可以保存什么？

### 4. 什么是 Tool Exchange Block？

- 追问 1：同一 AIMessage 有两个 tool calls 时如何分组？
- 追问 2：孤立 ToolMessage 应怎样处理？

### 5. Trace 的 event key 解决什么问题？

- 追问 1：为什么随机 UUID 不适合作为业务幂等键？
- 追问 2：数据库唯一约束与应用层检查各有什么作用？

## 二、源码题（6～10）

### 6. 沿源码说明 `AsyncSqliteSaver` 的创建和关闭链路。

- 追问 1：为什么不能在模块 import 时打开 SQLite？
- 追问 2：为什么异步 API 不使用同步 `SqliteSaver`？

### 7. 沿源码说明 pending approval 如何跨重启 resume。

- 追问 1：为什么进入进程锁后要再次读取 checkpoint？
- 追问 2：proposal ID 在哪里校验？

### 8. `RuntimeStore.list_runs()` 如何保证安全稳定分页？

- 追问 1：为什么 cursor 同时含 updated_at 与 run_id？
- 追问 2：如何避免 SQL 注入式排序与 where 条件？

### 9. `ContextManager` 如何保证 Tool Call 协议完整？

- 追问 1：ToolMessage 超限时为什么生成新 JSON，而不是字符串截断？
- 追问 2：必需 Block 仍超预算时怎样处理？

### 10. Trace 从节点到 SQLite 经过哪些组件？

- 追问 1：节点为什么不直接执行 SQL？
- 追问 2：interrupt 前的 approval event 如何避免重放重复？

## 三、设计取舍题（11～15）

### 11. 为什么 P6 使用两份 SQLite，而不是把所有内容塞进 checkpoint？

- 追问 1：两库带来了什么清理问题？
- 追问 2：为什么没有引入 ORM/Alembic？

### 12. 为什么 P6 选择确定性压缩而不是摘要 LLM？

- 追问 1：确定性压缩牺牲了什么？
- 追问 2：什么情况下才值得评估摘要模型？

### 13. 为什么不用自定义 EventBus 或强制 LangSmith？

- 追问 1：本地 Trace 能解决哪些问题？
- 追问 2：它不能替代哪些生产可观测能力？

### 14. 为什么 SQLite checkpoint 明确声明“未加密”？

- 追问 1：文件权限为什么不等于应用级加密？
- 追问 2：只加密 Trace 为什么不够？

### 15. 从 SQLite 升级 Postgres 不能只替换连接字符串，为什么？

- 追问 1：多实例 resume 需要新增哪些一致性设计？
- 追问 2：哪些接口契约应保持不变？

## 四、持久化、安全和排错题（16～20）

### 16. 应用重启后 GET 能看到 run，但 decision 返回 checkpoint_not_found，如何排查？

- 追问 1：是否应根据 runs 表重建一个猜测 State？
- 追问 2：是否应自动删除该 run？

### 17. 恢复时发现 `state_schema_version=2`，当前只支持 1，应该怎样处理？

- 追问 1：为什么不能补几个默认字段继续？
- 追问 2：未来正确迁移流程是什么？

### 18. ApprovalNode 重放后出现两条 `approval_requested`，根因可能是什么？

- 追问 1：应检查哪条数据库约束？
- 追问 2：修复时为什么不能让 duplicate insert 终止 Graph？

### 19. Context 压缩后模型报 ToolMessage ID 不匹配，如何定位？

- 追问 1：哪些消息绝不能单独删除？
- 追问 2：怎样用故障注入验证修复？

### 20. DELETE run 在删完 checkpoint 后 runtime DB 失败，怎样恢复？

- 追问 1：为什么不能承诺跨两库原子事务？
- 追问 2：为什么 running/awaiting approval 不允许删除？
