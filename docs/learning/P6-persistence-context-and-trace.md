# P6：持久化恢复、上下文治理与结构化 Trace

## 1. P6 解决的问题

P5 的修复闭环只能依赖进程内 checkpointer。P6 让等待审批和第二轮修复可以跨应用重启恢复，并增加安全运行索引、瞬时模型上下文治理、本地结构化 Trace 与终态清理。

## 2. Checkpoint 与普通数据库记录的区别

Checkpoint 保存可恢复的 LangGraph 状态、下一节点和内部元数据，是工作流事实来源。`runs` 表只保存查询摘要，不保存 messages，也不能反向覆盖 checkpoint。

## 3. thread_id 与 run_id

`run_id` 是 HTTP/API 标识，`thread_id` 是 LangGraph checkpoint 分区标识。P6 两者值相同，但在 `runs` 表中分列，避免未来语义混淆。

## 4. AsyncSqliteSaver

生产 Graph 只接收 lifespan 创建的 `AsyncSqliteSaver`。异步 start、resume、query 与 `adelete_thread()` 共用同一 saver 生命周期；生产源码没有 `InMemorySaver` 回退。

## 5. FastAPI lifespan

`create_app()` 本身不打开数据库。进入 lifespan 后创建数据目录、连接两份 SQLite、设置 busy timeout/foreign keys/WAL、迁移 runtime schema、初始化 saver；退出时关闭两条连接。

## 6. 进程重启恢复

重启测试会关闭旧连接，再以同一数据库创建新的 resources、Graph、Service、模型与锁。恢复依据只有数据库中的 run/thread 和 checkpoint，不依赖旧 Python 对象。

## 7. interrupt 恢复

恢复顺序是：查 RuntimeStore、读 checkpoint、校验状态版本、确认 pending proposal、校验 proposal ID、获取新进程锁、再次读取、用同一 thread 调用 `Command(resume=...)`。

## 8. Checkpoint State

完整 `AgentState` 仍含 messages、提案、测试记录、应用记录、Review 和 Final Report。Checkpoint 可能含源代码、Diff 和测试反馈，因此它不是脱敏数据库。

## 9. RuntimeStore

RuntimeStore 用参数化 SQL 管理 `schema_info`、`runs` 与 `trace_events`。它没有 ORM、Repository 基类、自动迁移框架或原始 SQL 查询 API。

运行索引中的 Final Report 也采用字段白名单，并明确排除来自模型的 `model_final_text`，避免把可能包含源码的模型原文从 checkpoint 再复制一份到查询索引。无状态变化的 GET 不回写 `updated_at`，因此读取不会扰动列表排序和 cursor。

## 10. 为什么需要 runs 表

Checkpoint 适合按 thread 恢复，不适合直接承担按状态过滤、更新时间排序、游标分页和安全摘要。`runs` 是可重建的查询索引，GET 单个运行仍会对照最新 checkpoint。

## 11. 数据库 schema migration

空库显式创建 v1；重复启动识别 v1；缺失或未知版本 fail closed。P6 不执行 destructive migration，也不猜测转换未来 schema。

## 12. state_schema_version

每个新 State 写入整数版本 `1`。query、decision resume 和恢复都验证它；缺失或未知版本返回 `checkpoint_incompatible`，不会继续 Apply 或 pytest。

## 13. checkpoint compatibility

P4/P5 使用的内存 checkpoint 没有落盘迁移问题。从 P6 开始，State 结构变化必须设计显式迁移，不能在读取时静默补默认值。

## 14. SQLite 未加密

checkpoint 与 runtime SQLite 都未加密。文件系统 ACL 只是保护的一部分；当前实现不适合直接保存高敏感生产代码，也没有伪造“字符串加密”。

## 15. 单进程限制

当前锁只序列化一个进程内的重复 decision。SQLite 和本地锁不解决多实例竞争、领导者选举或分布式一致性。

## 16. transient 与 persistent context

Persistent context 是 checkpoint 中完整 `state["messages"]`。Transient context 是 ModelNode 每次调用前由 ContextManager 构造的临时消息列表，只影响这一次模型输入。

## 17. Context Block

普通 Human/System/无工具 AI 消息各自成 Block；一个带 tool calls 的 AIMessage 和按顺序匹配的全部 ToolMessage 组成不可拆分的 Tool Exchange Block。

## 18. Tool Call 协议

多 tool call 必须有相同顺序的全部 ToolMessage，ID 不可改。孤立 ToolMessage、缺失结果或 ID 不匹配都会产生 `context_protocol_error`，而不是发送非法历史给模型。

## 19. 确定性压缩

策略保留初始 Goal、最近 Block、最近审批/Patch/测试/错误 Block；旧 Block 只形成基于工具名称和计数的确定性摘要；超大 ToolMessage 改成有效 JSON 统计，不调用模型。

## 20. 字符预算

P6 用 LangChain message 序列化后的字符数作为近似预算。协议安全的必需 Block 仍超限时返回 `context_budget_exceeded`；recursion limit 不承担上下文限制。

## 21. 为什么不调用摘要 LLM

摘要 LLM 会增加费用、延迟、不可重复性和敏感内容再暴露面。P6 需要先证明确定性裁剪可测试，P7 也不会自动改变这一安全结论。

## 22. 原始 State 为什么不能裁剪

永久裁剪会破坏恢复、审计、proposal/test 对齐和后续迁移。ContextManager 不修改输入列表，模型新消息仍由 reducer 追加到完整 State。

## 23. Context stats

只保存消息数、字符数、压缩/删除 Block 数和压缩工具结果数。它不保存压缩前后消息副本、文件内容、Diff 或模型提示。

## 24. Trace event

事件覆盖 run start/resume、model/context、tool、approval、patch、tests、review、report、completion、failure 和 delete。节点只发业务元数据，Recorder 执行脱敏与写入。

## 25. event_key

event key 由模型轮次、proposal ID、tool call ID、attempt 或 outcome 等稳定事实构成；数据库使用 `UNIQUE(run_id, event_key)`。

## 26. interrupt 重放幂等

ApprovalNode 恢复时可能从 interrupt 节点重新进入。`approval_requested:{proposal_id}` 再次插入会被唯一约束识别为已记录，不形成第二条事件，也不让 Graph 失败。

## 27. 为什么不用 EventBus

P6 只有单进程本地审计需求。小型注入式 Recorder 足以解耦节点和 SQL，自定义 EventBus 会引入订阅、投递、重放和生命周期复杂度。

## 28. Trace 脱敏

payload 采用字段 allowlist，只接受短标量。messages、tool input/output、完整 Diff、proposed/original content、完整 pytest 输出、异常、环境、密钥、Base URL 和数据库路径均不进入 Trace。

## 29. 运行查询

单 run GET 对照 checkpoint 并返回安全 ApprovalView/FinalReport；列表只按固定 `updated_at DESC, run_id DESC` 排序，status 参数化过滤，limit 有上限，cursor 是服务生成的复合位置。

## 30. 运行删除

只允许终态。running/awaiting approval 返回 `409 run_not_terminal`，API 不接受数据库路径或 thread ID。

## 31. checkpoint cleanup

跨两份 SQLite 无法形成原子事务。流程标记 in-progress、异步删除 checkpoint thread、删除 Trace、软删除 registry；失败标记 failed，可重复执行，是 best-effort 幂等多步骤操作。

## 32. SQLite 与未来 Postgres

SQLite 适合单机、低并发 Demo。未来多实例需要支持异步持久化的共享 checkpointer、事务/锁策略、连接池和部署迁移；不能只把文件名替换成 DSN。

## 33. 与 KamaClaude Session/Trace/Context 的差异

定向参考只用于识别恢复、事件幂等和上下文预算问题。RepoPilot 没有迁移 JSONL Session、daemon、EventBus 或自研 compactor；checkpoint 由 LangGraph 提供，索引/Trace 和确定性上下文均独立实现。

## 34. 新增文件职责

- `persistence/lifecycle.py`：两份 SQLite 与 saver 生命周期。
- `persistence/migrations.py`：runtime schema v1。
- `persistence/runtime_store.py`：运行索引、Trace、分页和清理 SQL。
- `persistence/contracts.py`：持久化安全模型与错误。
- `context/contracts.py`：策略、统计和结果。
- `context/manager.py`：Block 分组和确定性裁剪。
- `tracing/contracts.py`：事件类型和事件 Schema。
- `tracing/sanitization.py`：payload allowlist。
- `tracing/recorder.py`：幂等写入。
- `tracing/nodes.py`：节点 Trace 装饰器。
- `scripts/demo_p6.py`：A/B/C 三次重启演示。

## 35. 重启前后的状态恢复快照

```text
Service A: status=awaiting_approval, model_calls=1, repair_attempts=0, proposal=patch-1
Service B: same run/thread, approve patch-1, pytest=1, model_calls=2,
           repair_attempts=1, status=awaiting_approval, proposal=patch-2
Service C: same run/thread, approve patch-2, pytest=0, repair_attempts=2,
           review=passed, final outcome=repaired
```

## 36. 完整 messages 与实际 model messages 对照

```text
checkpoint messages: 9 messages, 5797 characters（完整保留）
model messages:      6 messages, 1542 characters（本次瞬时视图）
dropped blocks:      2
compacted tool results: 2
```

模型输入仍是 LangChain Message；压缩不会写回 checkpoint 或 API。

## 37. 必须亲手复写的六段

建议总量约 300 行，控制在 280～320 行：

1. `open_persistence()` 与关闭流程，约 45 行；
2. RuntimeStore create/update/get/list，约 75 行；
3. restart resume 双读校验，约 45 行；
4. Tool Exchange Block 分组，约 40 行；
5. deterministic compaction 与预算，约 55 行；
6. TraceRecorder、sanitization 与唯一键，约 40 行。

## 38. 主动修改练习：checkpoint 应用级加密

只设计实验分支，不默认进入生产。加密边界应位于 checkpointer serializer/数据库写入之前，覆盖完整 checkpoint blobs，而不只是 Trace。密钥来自外部 secret manager 或进程注入，不能与密文放在同一 SQLite。密钥记录版本，轮换时支持新写新钥、旧读旧钥和离线重加密；已有 checkpoint 需要可回滚、断点续传的显式迁移。测试必须覆盖错误密钥、缺失旧密钥、轮换中断、部分迁移、损坏密文、重启恢复和删除。只加密 Trace 无法保护 messages、Patch 和测试反馈，因此没有解决主要风险。

## 39. 故障注入练习

逐项注入并先观察测试失败：restart 生成新 thread；每请求新 DB；resume 换 InMemorySaver；缺失版本强行恢复；删除 ToolMessage；只留 ToolMessage；修改持久 messages；取消预算；Trace 写完整 Diff；Trace 写完整 pytest；Approval replay 随机 event key；runs 覆盖 Graph State；只删 runs；只删 checkpoint；数据库失败静默回退内存。

## 40. 一分钟面试口述稿

“RepoPilot P6 把 P5 的内存恢复升级为 LangGraph AsyncSqliteSaver。run_id 是 API 标识，thread_id 是 checkpoint 分区，目前同值。完整 AgentState 是工作流事实，单独的 RuntimeStore 只保存可查询摘要和脱敏 Trace。FastAPI lifespan 创建并关闭两条 SQLite 连接，恢复时先查 registry，再读 checkpoint、校验 state schema 和 proposal，进程锁内二次读取后 Command resume，所以等待审批和第二轮修复都能跨重启。模型并不接收全部持久历史；纯 Python ContextManager 把 AI tool call 与全部 ToolMessage 作为原子 Block，在字符预算内保留 Goal、近期审批和测试反馈，只压缩旧工具结果，原 State 不变。Trace 用稳定 event key 与数据库唯一约束抵抗 interrupt 重放。SQLite 未加密，清理跨两库只能 best-effort，当前仍是单进程方案；多实例和 Postgres 属于后续显式迁移，不在 P6 偷做。”
