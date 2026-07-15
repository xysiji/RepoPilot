# P6 面试题答案

## 1. Checkpoint 与 runs 表

- 标准答案：checkpoint 保存完整可恢复 Graph State 和执行位置，是事实来源；`runs` 保存脱敏查询索引。GET 单 run 会读 checkpoint 并更新索引，索引不能覆盖 State。
- 当前源码位置：`persistence/runtime_store.py::RuntimeStore`，`services/agent_service.py::get_run`。
- 常见错误回答：“两个库互为备份，任意一个都能恢复。”
- 面试表达：“工作流事实和查询投影分离，投影可重建、不可反向写事实。”
- 追问 1：只读 runs 可能看到崩溃前的旧状态，所以单 run 查询对照 checkpoint。
- 追问 2：messages 含代码和工具结果，会扩大泄漏面并重复 checkpoint。

## 2. run_id 与 thread_id

- 标准答案：run_id 面向 API，thread_id 面向 LangGraph checkpoint 分区；P6 同值只是最小映射，不等于语义相同。
- 当前源码位置：`persistence/migrations.py` 的 runs schema，`agent_service.py::_config`。
- 常见错误回答：“它们是同一个字段的两个名字。”
- 面试表达：“值可相同，职责必须分开，恢复始终使用 registry 中的 thread_id。”
- 追问 1：同值减少第一版映射复杂度，并让 UUID run 天然隔离。
- 追问 2：新 thread 没有旧 checkpoint，resume 会报找不到或不兼容。

## 3. persistent 与 transient context

- 标准答案：完整 messages 持久化；ContextManager 为单次模型调用构造有界视图，不写回 State。
- 当前源码位置：`agent/state.py`，`context/manager.py::ContextManager.build`，`agent/nodes.py::ModelNode`。
- 常见错误回答：“压缩就是从数据库删除旧消息。”
- 面试表达：“持久层保证恢复，瞬时层控制模型成本，两者通过只读投影隔离。”
- 追问 1：永久删除会破坏审计、协议、恢复和后续迁移。
- 追问 2：只存计数、字符数和 block/tool 压缩数量。

## 4. Tool Exchange Block

- 标准答案：带 tool calls 的 AIMessage 与按调用顺序匹配的全部 ToolMessage 是一个原子 Block。
- 当前源码位置：`context/manager.py::_group_protocol_blocks`。
- 常见错误回答：“每条消息独立裁剪，保留最近几条即可。”
- 面试表达：“上下文裁剪的最小单位不是消息，而是协议完整的交换。”
- 追问 1：两个 call 必须紧随两个同序、同 ID 的 ToolMessage。
- 追问 2：孤立结果是 protocol error，不能猜测配对。

## 5. event key

- 标准答案：event key 用稳定业务事实标识同一事件，配合 `UNIQUE(run_id,event_key)` 抵抗 interrupt 重放。
- 当前源码位置：`tracing/nodes.py`，`persistence/migrations.py`。
- 常见错误回答：“每次写入生成 UUID 就能去重。”
- 面试表达：“随机 ID 标识写入尝试，稳定业务键才标识同一事实。”
- 追问 1：随机 UUID 每次重放不同，无法识别重复。
- 追问 2：应用生成语义键，数据库在竞争下做最终唯一裁决。

## 6. AsyncSqliteSaver 生命周期

- 标准答案：lifespan enter 调用 `open_persistence()`，打开异步连接、配置 PRAGMA、创建 strict serializer saver 并 `setup()`；exit 关闭两条连接。
- 当前源码位置：`api/app.py::lifespan`，`persistence/lifecycle.py`。
- 常见错误回答：“每次请求 `from_conn_string()` 打开并关闭。”
- 面试表达：“资源跟 App 生命周期走，不跟模块 import 或单次请求走。”
- 追问 1：import 打开会产生隐式 I/O、测试污染和无法控制的失败时机。
- 追问 2：同步 saver 会阻塞异步路径，也不符合当前 `ainvoke/aget_state` 链路。

## 7. restart resume

- 标准答案：查 registry、读 State、验版本/next/proposal、取新进程锁、二次读取，再用相同 thread_id 与 Command resume。
- 当前源码位置：`agent_service.py::resume_run`、`_validated_pending_snapshot`、`_resume_run_locked`。
- 常见错误回答：“拿到 run_id 直接重新 start。”
- 面试表达：“恢复不是重跑，是在持久 checkpoint 的下一执行点继续。”
- 追问 1：锁等待期间状态可能变化，二次读取防止同进程重复决定。
- 追问 2：锁前和锁内都由 `_validated_pending_snapshot` 校验 proposal。

## 8. list_runs 分页

- 标准答案：固定按 `(updated_at DESC, run_id DESC)`，cursor 编码这两个值，SQL 只拼接服务端固定片段，用户值全部参数化。
- 当前源码位置：`runtime_store.py::list_runs`、`_encode_cursor`、`_decode_cursor`。
- 常见错误回答：“客户端传 order_by 和 where 更灵活。”
- 面试表达：“稳定复合游标解决相同时间戳，固定排序面消除 SQL 控制面输入。”
- 追问 1：run_id 是相同 updated_at 的确定性 tie-breaker。
- 追问 2：API 只有 status、limit、cursor，没有原始 SQL 字段。

## 9. ContextManager 协议

- 标准答案：先严格分组，裁剪只按 Block；ToolMessage 超限改成包含统计的完整 JSON，call ID/name/status 保留。
- 当前源码位置：`context/manager.py::_group_protocol_blocks`、`_compact_tool_block`。
- 常见错误回答：“对序列化后的总字符串做切片。”
- 面试表达：“预算约束必须服从协议约束，不能为了 token 省一点制造非法消息。”
- 追问 1：直接截断 JSON 会使模型收到语法损坏的工具结果。
- 追问 2：必需原子 Block 超限返回 `context_budget_exceeded`。

## 10. Trace 写入链路

- 标准答案：Service 或 traced node 构造 TraceEvent，Recorder 调用 sanitizer，再由 RuntimeStore 参数化写入。
- 当前源码位置：`tracing/nodes.py`、`tracing/recorder.py`、`tracing/sanitization.py`。
- 常见错误回答：“节点把整个 State JSON 写数据库，之后查询再脱敏。”
- 面试表达：“先最小化再持久化，不把敏感数据先落盘后补救。”
- 追问 1：节点不懂表结构，便于业务测试和统一脱敏。
- 追问 2：interrupt 前用 proposal 派生稳定 key，重复插入返回已存在。

## 11. 两份 SQLite

- 标准答案：checkpoints.sqlite3 由 LangGraph 管 Graph State；runtime.sqlite3 由应用管理索引、Trace 和迁移。
- 当前源码位置：`persistence/lifecycle.py`、`persistence/migrations.py`。
- 常见错误回答：“拆库是为了高可用。”
- 面试表达：“拆分来自所有权和查询模型，而不是宣称分布式能力。”
- 追问 1：删除跨库不是原子事务，只能状态化 best-effort 重试。
- 追问 2：P6 只有三张小表，ORM/Alembic 会扩大依赖和抽象。

## 12. 确定性压缩

- 标准答案：纯 Python 的 Block 选择、旧结果摘要和字符预算，零额外模型调用。
- 当前源码位置：`context/manager.py`。
- 常见错误回答：“LLM 摘要语义最好，应该默认启用。”
- 面试表达：“第一目标是可恢复、可测和不泄漏，摘要质量让位于确定性。”
- 追问 1：牺牲旧细节和语义压缩率。
- 追问 2：只有可测的确定性策略不足且安全/费用边界明确后才评估。

## 13. 不用 EventBus/LangSmith

- 标准答案：本阶段只需本地单进程结构化审计，小 Recorder 足够；云 Trace 不是核心闭环依赖。
- 当前源码位置：`tracing/`，生产源码无 EventBus/LangSmith 配置。
- 常见错误回答：“SQLite Trace 已等价于完整 APM。”
- 面试表达：“保留事件语义，不迁移产品级事件基础设施。”
- 追问 1：可做 run 级顺序、失败和审批审计。
- 追问 2：不能提供跨服务传播、指标聚合、分布式采样和云分析。

## 14. SQLite 未加密

- 标准答案：数据库以明文文件形式存储，checkpoint 可能含代码、Diff 和测试反馈，必须依赖受控主机与文件权限并明确限制。
- 当前源码位置：`docs/security/TOOL_SAFETY_POLICY.md`，`docs/learning/P6-persistence-context-and-trace.md`。
- 常见错误回答：“SQLite 文件不公开，所以等于加密。”
- 面试表达：“访问控制和静态加密是两层不同控制，当前只具备前者的一部分。”
- 追问 1：有文件读取权限的人仍可直接读取明文。
- 追问 2：敏感主体在 checkpoint，不只在 Trace。

## 15. Postgres 升级

- 标准答案：需要兼容的 checkpointer、共享事务语义、连接池、迁移、并发 resume 锁和部署运维设计。
- 当前源码位置：`persistence/contracts.py` 与 AgentService 的持久化边界是未来替换面。
- 常见错误回答：“把 `.sqlite3` 改成 Postgres URL 即可。”
- 面试表达：“数据库升级的难点是并发语义，不是 SQL 方言。”
- 追问 1：需要跨实例幂等 decision、锁/租约和故障恢复。
- 追问 2：run/query/trace/cleanup 服务契约和 state version 应保持。

## 16. registry 有、checkpoint 无

- 标准答案：判定持久事实不一致，返回 `checkpoint_not_found`，排查数据库路径、thread_id、清理状态和连接生命周期。
- 当前源码位置：`agent_service.py::get_run`。
- 常见错误回答：“用 runs 字段拼一个 State 继续。”
- 面试表达：“不从摘要猜工作流事实，fail closed 并保留数据调查。”
- 追问 1：不能重建，runs 没有 messages、proposal content 和执行位置。
- 追问 2：不自动删除，避免掩盖配置或部分清理故障。

## 17. State 版本不兼容

- 标准答案：返回 `checkpoint_incompatible`，不 Apply、不 pytest、不删除，等待显式迁移工具。
- 当前源码位置：`agent/state.py::CURRENT_STATE_SCHEMA_VERSION`，`agent_service.py::_validate_state_version`。
- 常见错误回答：“TypedDict 多余字段会忽略，继续跑即可。”
- 面试表达：“可解析不等于语义兼容，副作用恢复必须显式版本门禁。”
- 追问 1：默认值可能改变审批、预算或重试事实。
- 追问 2：离线备份、版本识别、确定性转换、验证和回滚。

## 18. 重复 approval_requested

- 标准答案：检查 event key 是否稳定、run_id/proposal_id 是否一致，以及唯一索引是否为 `(run_id,event_key)`。
- 当前源码位置：`tracing/nodes.py::traced_sync_node`，runtime migration。
- 常见错误回答：“在内存 set 里记录已写事件。”
- 面试表达：“重启幂等必须落到持久唯一约束，不能依赖旧进程内存。”
- 追问 1：检查 `trace_events` 唯一约束与 INSERT OR IGNORE。
- 追问 2：duplicate 表示同一事实已记录，应视为成功继续。

## 19. ToolMessage ID 错位

- 标准答案：检查 Block 分组是否按 AI tool_calls 顺序逐个消费 ToolMessage，是否拆分、多删或改 ID。
- 当前源码位置：`context/manager.py::_group_protocol_blocks`。
- 常见错误回答：“找最近一个同名工具结果配上。”
- 面试表达：“协议关联靠 call ID 和顺序，不靠工具名或文本猜测。”
- 追问 1：AIMessage 与其全部 ToolMessage 都不可单独删。
- 追问 2：注入孤立、缺失、乱序、多 call 少结果并断言 protocol error。

## 20. 跨库清理部分失败

- 标准答案：runs 标记 cleanup，重复 `adelete_thread`，再删 Trace/软删 registry；失败保留 failed 状态并返回 `run_cleanup_failed`。
- 当前源码位置：`agent_service.py::delete_run`，`runtime_store.py::mark_cleanup/delete_runtime_data`。
- 常见错误回答：“两份 SQLite 可以自动组成一个事务。”
- 面试表达：“承认事务边界，用幂等步骤和显式状态恢复，不假装原子。”
- 追问 1：两个独立连接/文件没有应用当前实现可用的跨库事务。
- 追问 2：活跃 run 删除会使审批、Apply 和 checkpoint 状态悬空，故返回 409。
