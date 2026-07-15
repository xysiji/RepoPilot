# P4：Patch 提案与人工审批

## 1. P4 解决的问题

P3 能安全读取代码，但没有合法写入口。P4 在不开放通用写文件和命令的前提下，建立“模型提案—系统 Diff—人工审批—确定性应用—ToolMessage 回填”的单文件闭环。

## 2. 为什么模型不能直接写文件

模型输出受任务文本和仓库内容影响，不是可信执行环境。它可以选择修改内容，但路径、审批、哈希、资源限制和写入次数必须由 Python 判定。模型只看到 `propose_patch`，看不到 `write_file` 或 `apply_patch`。

## 3. Proposal 与 Apply 的区别

Proposal 是只读计算：读取一个既有文本文件，计算 Diff 和哈希并写入 State。Apply 是副作用：只在有效 approve 后重新校验并原子替换。Proposal 成功不代表文件已改变。

## 4. 完整执行链

```text
AIMessage(propose_patch)
→ Dispatch → Validation → Policy(require_approval)
→ PatchProposalBuilder
→ pending_approval
→ ApprovalNode.interrupt(payload)
→ Command(resume=decision)
→ ApplyPatchNode 或 RejectPatchNode
→ ToolMessage(original tool_call_id)
→ ModelNode
```

## 5. 为什么由系统计算 Diff

模型提交 path、完整 new_content 和 rationale。系统以真实 current content 计算 unified diff，避免 raw patch 的 hunk 偏移、模糊解析和“展示 A、应用 B”。Apply 前会再次计算 Diff 并与 Proposal 比较。

## 6. PatchProposal 每个字段

- `proposal_id`：本次审批对象的服务端 UUID。
- `tool_call_id`/`tool_name`：恢复 ToolMessage 协议。
- `relative_path`：规范化工作区相对路径。
- `rationale`：供人审阅读的限长理由。
- `original_sha256`/`proposed_sha256`：审批前后内容身份。
- `unified_diff`：完整、未截断审查内容。
- `original_character_count`/`proposed_character_count`：资源审计。
- `added_line_count`/`removed_line_count`：变更规模。
- `created_at`：提案时间。
- `proposed_content`：仅内部恢复/应用使用，不单独返回 API。

## 7. original/proposed Hash

两个 SHA-256 分别绑定读取时原文和批准后要写的 UTF-8 字节。original 用于乐观并发检查，proposed 用于写前完整性和写后验证；它们不能代替路径与 Diff 复核。

## 8. stale_patch

审批期间如果目标文件字节发生变化，当前 SHA-256 不再等于 `original_sha256`，Apply 返回 `stale_patch` 并停止。系统不会用旧批准覆盖用户的新修改。

## 9. 为什么 Diff 不能截断后审批

审批授权的对象必须完整可见。Diff 超过固定上限时直接拒绝 Proposal；若展示截断内容却应用全部内容，用户没有授权隐藏部分。

## 10. write effect 如何进入 require_approval

可信 mapping 把三个读取工具标为 `read_only`、`propose_patch` 标为 `write`。Policy 只允许显式注册的该工具返回 `require_approval`；其他 write、command 和 unknown 都返回 deny。

## 11. Approval Node

`ApprovalNode` 只读取 checkpointed Proposal、构造安全 view、调用 `interrupt()`、恢复后校验决定。它不生成 UUID、不读写目标、不调用模型、不维护独立 pending 注册表。

## 12. `interrupt()`

动态 interrupt 把 JSON payload 暴露给调用者并让图暂停。P4 不使用 static `interrupt_before`，因为审批点和载荷属于节点业务逻辑。

## 13. `Command(resume=...)`

Service 在 API schema、run、pending、proposal_id 全部校验后才构造 `Command(resume=validated_json)`。客户端不能传 Command、goto 或 State update。

## 14. thread_id

服务端生成 UUID `run_id`，同值作为 `configurable.thread_id`。start 和 resume 必须使用同一值；API 不提供独立 thread_id 字段。

## 15. checkpointer

Graph 编译时注入 checkpointer。它保存 interrupt 前 State 和执行位置，使下一次 HTTP 请求能恢复同一节点；Graph、checkpointer、工具对象本身不进入 State。

## 16. InMemorySaver 限制

P4 使用 `InMemorySaver`，只支持同一进程、同一 AgentService 生命周期。换一个 saver 或重启进程后旧 run 返回 `run_not_found`。Service 用 run 级异步锁串行化同进程内的“状态复核 + resume”，但这不是跨进程分布式锁。P6 才引入 SQLite 并重新设计持久并发控制。

## 17. 为什么 interrupt node 会重新执行

LangGraph 恢复时从包含 `interrupt()` 的节点开头重跑，并把 resume 值作为该调用返回值。因此 interrupt 前代码必须确定、可重复且无副作用。

## 18. 为什么 interrupt 前不能写文件

若先写再 interrupt，恢复会再次执行写入，拒绝也已经太晚。P4 在 Approval Node 之前只创建 JSON Proposal，真实写入位于后续 ApplyPatch Node。

## 19. 为什么 Apply 使用独立节点

独立节点让路由可证明：只有有效 approve 才可到写入边，reject/invalid 到零写入节点。它也把重放安全和文件系统异常测试从 Approval Node 分离。

## 20. Approval Payload 脱敏

Payload 包含 run/proposal/tool-call ID、相对路径、理由、完整 Diff、哈希、字符和行数。它不含 API key、base URL、绝对 workspace、original content、单独 proposed content、临时路径或 traceback。

## 21. approve/reject

决定只有两个枚举。approve 进入 Applicator；reject 生成稳定 error ToolMessage，文件逐字节不变。非法或 mismatched resume 在 API 前置校验或节点防御校验中失败。

## 22. ToolMessage 补齐

暂停期间原 AIMessage 暂无 ToolMessage。恢复后 Apply/Reject 为原调用生成且只生成一个 ToolMessage，再回到模型，保证 Provider 协议完整。

## 23. proposal_id 与 tool_call_id 的区别

`tool_call_id` 属于模型消息协议，用来配对 AIMessage/ToolMessage；`proposal_id` 属于审批授权，用来确认用户决定对应哪份 Patch。两者都必须保留，不能互相替代。

## 24. 重复审批

成功/拒绝后 State 清空 pending，checkpoint 的 `next` 为空或进入后续状态。Service 在 resume 前检查 snapshot；run 级异步锁覆盖检查和 `Command(resume=...)`，所以顺序或同进程并发重复决定都会在第二次复核时得到 `run_already_completed`，不会再次调用 Apply。

## 25. Hash 冲突

冲突不是“写失败后重试”，而是批准对象已过期。系统返回 `stale_patch` 给模型，不自动重建或应用；新内容需要新 Proposal 和新批准。

## 26. 原子写入

Apply 在目标同目录写临时文件，flush、fsync、关闭后保留原权限并 `os.replace`。replace 前失败时原目标未被半写；replace 后再核对 proposed hash。

## 27. 临时文件

同目录避免跨文件系统 replace。Windows 要求 replace 前关闭 handle。异常路径在 `finally` 尽力删除临时文件，API 不返回临时路径。

## 28. 为什么 P4 不是事务数据库

P4 只有单文件原子替换，没有事务日志、跨资源提交、durable idempotency 或崩溃恢复。它明确不声称提供多文件 ACID 或跨进程 exactly-once。

## 29. 为什么不支持多 Patch

多文件或同轮多个副作用需要批次授权、顺序、半失败语义和回滚策略。P4 用“patch 必须是唯一 tool call”保持批准对象和一次写入一一对应。

## 30. 为什么不允许客户端编辑 Patch

客户端编辑会让 checkpoint 中的 Diff、hash 和 proposed content 失配。P4 只允许 approve/reject；编辑应成为全新 Proposal，重新计算并使旧 ID 失效。

## 31. Graph 拓扑变化

P3 的 `model ↔ tools` 增加 `approval`、`apply_patch`、`reject_patch`。四个纯路由决定模型终止、工具后审批、审批分支和结果回填；没有静态 interrupt 或隐藏跳转。

## 32. max_steps 特殊规则

`max_steps` 仍表示模型调用轮次。如果最后一轮出现 `propose_patch`，ToolNode 返回 `approval_not_started_budget_exhausted` 和配对 ToolMessage，状态为 max steps，绝不 interrupt 或写入。

## 33. 与 KamaClaude Permission 的差异

共同点是参数先校验、人工决定在副作用前、拒绝作为工具反馈。RepoPilot 不迁移 daemon、Future、EventBus、TUI、always cache、命令正则和自动重试；使用 LangGraph checkpoint/interrupt，并把批准绑定到完整单文件 Proposal。

## 34. P5 将如何接入 pytest

P4 当时只规定“成功 apply 后增加固定 pytest 节点和有限反馈路由”。P5 现已实现该演进：成功 Apply 不再立即生成 success ToolMessage，而是把安全上下文交给 Tester；Tester 完成固定 pytest 后才用原 tool_call_id 生成唯一 Patch+Test ToolMessage。reject、stale 和 Apply failure 的即时 error ToolMessage 语义不变。测试 exit code 由 Python 判定，任何新修复 Patch 都必须再次审批。

## 35. 每个新增文件职责

- `approval/contracts.py`：审批输入、安全 view、决定和稳定服务错误。
- `approval/validation.py`：防御性 resume 校验。
- `patching/proposal.py`：安全读取、Diff/hash/限制与 Proposal。
- `patching/applicator.py`：审批后复核和原子替换。
- `tools/patch.py`：唯一模型可见的 Proposal Schema；函数本身不写。
- `agent/state.py`：run/pending/decision 状态。
- `agent/nodes.py`：批次限制、interrupt、apply/reject 和 ToolMessage。
- `agent/routing.py`：四个只读路由。
- `agent/graph.py`：五节点与 checkpointer 编译。
- `services/agent_service.py`：UUID start、snapshot precheck 和同 thread resume。
- `api/routes/agent.py`：200/202/404/409/422 HTTP 边界。
- `scripts/demo_p4.py`：无网络 approve/reject 真 interrupt 演示。

## 三个状态快照

```text
提案前：
status = running
pending_approval = None
```

```text
interrupt 时：
status = awaiting_approval
pending_approval = PatchProposal JSON（含内部 proposed_content）
```

```text
approve 后：
pending_approval = None
messages += ToolMessage(success)
tool_executions += patch record
```

## 必须亲手复写（约 260 行）

1. `ProposePatchInput`：约 30 行，重点是 extra forbid、不 strip 代码、NUL/长度边界。
2. `PatchProposalBuilder`：约 75 行，重点是 existing/UTF-8/limits/Diff/hash/零写入。
3. `ApprovalNode`：约 30 行，重点是 payload、interrupt 和防御校验。
4. `ApplyPatchNode` 与 Applicator 主链：约 70 行，重点是 stale、Diff 绑定、temp/replace/post hash。
5. `AgentService.start_run/resume_run`：约 55 行，重点是 UUID、thread config、snapshot 校验和 Command。

先手写不变量和 Spy/字节断言，再复写实现；不要抄整文件。

## 36. 主动修改练习：允许用户 edit 后再批准

设计但不默认进入生产：API 接受 edit 后不能直接覆盖 pending State。服务应把编辑内容当作新输入，重新读取当前文件、计算完整 Diff/双 hash/资源限制并生成新 proposal_id；旧 Proposal 标记失效，旧批准不可恢复。测试至少覆盖批准 A 不会应用 B、旧 ID 409、编辑后路径仍不可变、超限 edit 拒绝、等待期间 stale、新 Proposal ToolMessage ID 策略以及 API 不泄漏内容。

## 37. 故障注入练习

| 故障 | 风险/表现 | 定位证据 |
| --- | --- | --- |
| interrupt 前写文件 | reject 也已产生副作用，resume 可能重复写 | pause 后逐字节断言 |
| Approval Node 每次生成 UUID | 用户看到的 ID 无法恢复 | resume 前后 proposal_id 比较 |
| resume 使用新 thread_id | 找不到 pending 或串到空状态 | snapshot/run_not_found 测试 |
| 客户端能传 new_content | 批准 A、应用 B | API extra forbid 422 |
| 不校验 proposal_id | 跨 run 批准 | 两 pending run 交叉测试 |
| 不复核 original hash | 覆盖审批期间人工修改 | stale_patch 测试 |
| 截断 Diff 仍批准 | 隐藏修改未授权 | diff limit 必须拒绝 |
| reject 仍调用 applicator | 拒绝后文件改变/出现 temp | applicator Spy 与字节断言 |
| Tool Call ID 丢失 | Provider 无法关联结果 | 原 ID 精确断言 |
| apply 后无 ToolMessage | 下一轮消息协议断裂 | 模型 received_messages 断言 |
| temp 未关闭就 replace | Windows replace 失败 | replace hook 尝试打开 temp |
| replace 失败留 temp | 工作区污染/秘密路径泄漏 | glob 临时前缀 + 响应脱敏 |
| 同一 resume 应用两次 | 重复副作用 | 第二次 409、replace 计数 1 |
| 把 InMemorySaver 称持久化 | 重启后承诺失真 | 新 saver 的 run_not_found 测试 |
| max_steps 耗尽仍审批 | 写完却无模型反馈预算 | 最后一轮无 interrupt/零写入 |

## 38. 一分钟面试口述稿

“P4 在 P3 安全管线后只加了一个 `propose_patch`。模型提交单个既有文件的完整新内容，但不能写；Python 先做 Schema、WorkspaceGuard、UTF-8 和资源校验，再从真实 old/new content 计算完整 unified diff、双 SHA-256 和服务端 proposal_id。Proposal 进入 LangGraph checkpoint，Approval Node 是唯一 interrupt 调用者，而且 interrupt 前零副作用。API 用服务端 run_id 作为 thread_id，只接受匹配 proposal_id 的 approve/reject。恢复后有效批准进入独立 Apply 节点，再次复核路径、链接、original hash、proposed hash 和 Diff 绑定；文件变化返回 stale。写入使用同目录 temp、flush/fsync/close 和 os.replace，写后再验 hash。拒绝生成 error ToolMessage 且字节不变，批准生成 success ToolMessage，两者都保留原 tool call ID。P4 的 InMemorySaver 只支持进程内恢复，不是长期记忆；SQLite、pytest 循环和多文件事务都留到后续阶段。”

## 39. P6 后续演进补充

P4 的审批 interrupt 已在 P6 由磁盘 AsyncSqliteSaver 持久化；恢复不再依赖旧进程 Graph、锁或 InMemorySaver。proposal、State 版本和文件 preimage 仍必须重新校验，持久化不会扩大审批授权。
