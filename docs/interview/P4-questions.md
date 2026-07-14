# P4 面试题：Patch 提案与人工审批

本题集围绕 RepoPilot P4 的单文件 Patch 提案、LangGraph 中断恢复、人工审批和安全应用，共 20 道主问题；每题包含两个递进追问。

## 一、概念题（5 题）

### 1. 为什么要把 Patch Proposal 与 Patch Apply 分成两个阶段？

- 追问 1：为什么模型不能直接调用真正写文件的工具？
- 追问 2：在 Proposal 创建后、审批之前，系统必须保持什么不变量？

### 2. LangGraph 的 human-in-the-loop interrupt 在 P4 中解决了什么问题？

- 追问 1：`interrupt()` 与普通函数返回有什么本质区别？
- 追问 2：为什么调用 `interrupt()` 的节点在恢复时必须允许重放？

### 3. `Command(resume=...)`、checkpointer 和 `thread_id` 分别承担什么职责？

- 追问 1：只保存 `run_id`，但不使用相同 checkpointer，能否恢复？
- 追问 2：为什么 P4 的 `run_id` 与 LangGraph `thread_id` 使用同一个服务端 UUID？

### 4. 原文件哈希、目标内容哈希和完整 unified diff 为什么要同时保存？

- 追问 1：只检查原文件哈希能否防止审批内容被替换？
- 追问 2：为什么 Apply 前还要重新计算并核对 diff 与行数？

### 5. 什么是 P4 中的“同目录原子替换”？

- 追问 1：为什么临时文件必须与目标文件位于同一目录？
- 追问 2：原子替换是否等价于完整事务和自动回滚？

## 二、源码题（5 题）

### 6. `propose_patch` 的输入如何被限制，为什么它没有 `approved` 或 `apply` 字段？

- 追问 1：Pydantic 的 `extra="forbid"` 在这里防止什么攻击或误用？
- 追问 2：直接调用该 LangChain Tool 是否可能写文件？

### 7. Tool Node 遇到合法 `propose_patch` 时如何进入等待审批状态？

- 追问 1：为什么中断前不能提前生成成功 `ToolMessage`？
- 追问 2：同一 AIMessage 同时包含 patch 和读取调用时为什么整批拒绝？

### 8. Approval Node 如何构造中断载荷并验证恢复值？

- 追问 1：为什么 API 返回的是 `ApprovalRequestView`，而不是完整 `PatchProposal`？
- 追问 2：无效恢复值为什么会被转换为拒绝，而不是默认批准？

### 9. Apply Patch Node 与 Reject Patch Node 如何维持 Tool Calling 协议？

- 追问 1：审批结束后生成的 `ToolMessage.tool_call_id` 来自哪里？
- 追问 2：为什么批准失败（例如 stale）仍必须生成一个错误 ToolMessage？

### 10. `AgentService.resume_run()` 如何阻止错 run、错 proposal 和重复审批？

- 追问 1：并发提交两个相同 approve 时，为什么不会应用两次？
- 追问 2：为什么状态检查和 `Command(resume=...)` 必须处于同一个按 run 加锁的临界区？

## 三、设计取舍题（5 题）

### 11. 为什么 P4 使用 `InMemorySaver`，而不立即使用 SQLite？

- 追问 1：这一取舍牺牲了什么恢复能力？
- 追问 2：P6 换成 SQLite 后，为什么仍不能省略文件哈希与路径复核？

### 12. 为什么 P4 让模型提交完整新内容，而不是提交 diff hunk？

- 追问 1：这种设计的主要成本是什么？
- 追问 2：固定的内容、diff 和修改行数上限如何控制成本？

### 13. 为什么 P4 一轮只允许一个 patch 调用，且不允许与其他工具混合？

- 追问 1：这与“执行同一 AIMessage 中全部工具调用”的一般规则如何协调？
- 追问 2：未来支持多文件 Patch 时需要增加哪些事务语义？

### 14. 为什么 P4 选择临时文件加 `os.replace()`，但不实现备份和回滚？

- 追问 1：直接以写模式覆盖目标文件有什么风险？
- 追问 2：替换完成后的哈希验证失败时，P4 能提供什么、不能提供什么？

### 15. 为什么审批 API 返回完整 diff，却不返回完整 proposed content？

- 追问 1：Health 和 Agent API 还需要避免暴露哪些信息？
- 追问 2：如果 diff 超过限制，系统应该截断后让用户批准，还是拒绝创建 Proposal？

## 四、安全与排错题（5 题）

### 16. 用户审批期间文件被外部程序修改，系统会发生什么？

- 追问 1：这个失败应该归类为什么错误码？
- 追问 2：系统是否应该自动基于新文件重放旧批准？

### 17. 客户端提交错误 proposal_id、额外字段或伪造新内容时如何处理？

- 追问 1：哪些问题在 HTTP Schema 层拦截，哪些在服务层拦截？
- 追问 2：为什么决定请求不能携带 path、diff 或 proposed content？

### 18. 同一个审批被顺序或并发提交两次时如何排错？

- 追问 1：顺序重复与并发重复分别由哪一层阻止？
- 追问 2：如果未来部署多个进程，当前进程内锁是否足够？

### 19. 模型在最后一个允许轮次才发出 `propose_patch`，系统为什么不进入审批？

- 追问 1：对应的稳定错误码是什么？
- 追问 2：为什么不能让审批绕过 `max_steps` 后再偷偷增加模型轮次？

### 20. 服务重启后旧 `run_id` 无法恢复，应如何判断是缺陷还是已知限制？

- 追问 1：P4 API 应返回什么稳定结果？
- 追问 2：P6 引入持久 checkpointer 后，还需要处理哪些重启后的外部状态变化？
