# P4 面试题答案：Patch 提案与人工审批

本文件对应 `docs/interview/P4-questions.md`。源码位置以当前 P4 的文件和符号为准。

## 一、概念题答案

### 1. Patch Proposal 与 Patch Apply

- **标准答案**：Proposal 是无副作用的审查对象，负责固定相对路径、完整 diff、修改前后哈希、行数、理由和工具调用 ID；Apply 是批准后才发生的确定性副作用。分层后，模型只能提议，Python 代码负责边界复核与写入。
- **真实源码位置**：`src/repopilot/patching/proposal.py::PatchProposalBuilder`；`src/repopilot/patching/applicator.py::PatchApplicator.apply`。
- **常见错误回答**：“模型生成内容后直接写，失败再撤回。”这会让审批发生在副作用之后，且 P4 没有可靠回滚。
- **面试表达**：我把“想改什么”和“是否真的改”拆成两个能力域；Proposal 可审计且零写入，Applicator 是唯一写入点。
- **追问 1 答案**：模型输出不可信，也不能可靠判断路径越界、stale、批准绑定或原子写入；真正写入必须由确定性代码控制。
- **追问 2 答案**：目标文件字节必须不变，不得出现成功 ToolMessage，不得丢失 tool_call_id，pending Proposal 必须可由 checkpointer 恢复。

### 2. human-in-the-loop interrupt

- **标准答案**：`interrupt()` 把图暂停在可持久化边界，向调用方返回安全审批载荷；之后使用同一 thread 的 `Command(resume=...)` 恢复，让审批成为工作流状态的一部分，而不是进程外的临时布尔值。
- **真实源码位置**：`src/repopilot/agent/nodes.py::ApprovalNode`；`src/repopilot/agent/graph.py::build_agent_graph`。
- **常见错误回答**：“interrupt 就是抛异常，捕获后重新调用接口即可。”这忽略了 checkpoint 与恢复游标。
- **面试表达**：中断不是结束运行，而是把控制权显式交给人，同时保留可恢复的图状态。
- **追问 1 答案**：普通 return 会沿后续边继续执行；`interrupt()` 会形成挂起任务并把载荷放入图结果，直到收到 resume 命令。
- **追问 2 答案**：LangGraph 恢复时从该节点开头重新执行，所以 interrupt 之前不能写文件、发外部消息或更新不可重放的独立状态。

### 3. Command、checkpointer 与 thread_id

- **标准答案**：`Command(resume=...)` 提供人工决定；checkpointer 保存状态和挂起游标；`thread_id` 选择要恢复的那条状态线。三者缺一不可。
- **真实源码位置**：`src/repopilot/services/agent_service.py::AgentService.resume_run`、`_config`；`src/repopilot/agent/graph.py::build_agent_graph`。
- **常见错误回答**：“run_id 是数据库主键，有它就能恢复。”P4 没有持久数据库，run_id 只有和原 checkpointer 配合才有意义。
- **面试表达**：resume 数据回答“人怎么决定”，thread_id 回答“恢复哪次运行”，checkpointer 回答“从什么状态继续”。
- **追问 1 答案**：不能；换成新的 `InMemorySaver` 后没有对应 checkpoint，服务稳定返回 `run_not_found`。
- **追问 2 答案**：服务端 UUID 不接受客户端选 thread，避免碰撞和越权；一一映射也减少 run/thread 身份错配。

### 4. 双哈希与完整 diff

- **标准答案**：original hash 用于发现等待期间的外部变化，proposed hash 绑定实际要写的内容，完整 diff 是用户批准的审查表示。Apply 前重新计算哈希、diff 和行数，确保“批准的展示内容”与“将写入的字节”一致。
- **真实源码位置**：`src/repopilot/patching/proposal.py::PatchProposal`、`proposal_review_matches_content`；`src/repopilot/patching/applicator.py::PatchApplicator.apply`。
- **常见错误回答**：“有原文件哈希就足够。”它只能防 stale，不能防 pending 状态中的新内容或 diff 被篡改。
- **面试表达**：我做了两条绑定：原始哈希绑定读取基线，目标哈希和重算 diff 绑定批准对象与实际写入对象。
- **追问 1 答案**：不能；攻击者仍可能替换 proposed content，同时保留原始哈希。
- **追问 2 答案**：避免仅校验目标哈希却向用户展示另一份 diff，也避免被篡改的统计信息误导审批。

### 5. 同目录原子替换

- **标准答案**：先在目标目录创建临时文件，完整编码写入、flush 和 `fsync`，复制权限位，然后以 `os.replace()` 一次替换目标，最后重读校验目标哈希。
- **真实源码位置**：`src/repopilot/patching/applicator.py::PatchApplicator.apply`。
- **常见错误回答**：“`open(path, 'w')` 也是一次写入，所以是原子的。”进程中断时可能留下截断或部分内容。
- **面试表达**：同目录临时文件让 replace 在同一文件系统内完成，缩短目标处于不一致状态的窗口。
- **追问 1 答案**：跨文件系统移动不一定具备原子替换语义，甚至可能退化为复制与删除。
- **追问 2 答案**：不等价；P4 不做备份、跨文件事务或 replace 后回滚，只提供单文件替换和事后校验。

## 二、源码题答案

### 6. propose_patch 输入边界

- **标准答案**：输入只有 `path`、`new_content`、`rationale`，并限制 NUL、空值和长度；客户端控制的批准、哈希、diff、tool_call_id 都不在 Schema 中，均由服务端生成。
- **真实源码位置**：`src/repopilot/tools/contracts.py::ProposePatchInput`；`src/repopilot/tools/patch.py::build_patch_tool`。
- **常见错误回答**：“加一个 `approved: bool`，为 true 就写。”模型能够伪造这个字段，破坏人工审批边界。
- **面试表达**：模型只描述意图，所有授权字段和完整性字段都由可信代码派生。
- **追问 1 答案**：拒绝伪造的 `approved`、`proposal_id`、hash 或其他未知控制字段，避免 mass-assignment 风格绕过。
- **追问 2 答案**：不能；工具函数只返回 `approval_required` 的稳定结构，Proposal 构造由安全 Executor 完成，写入只在 Applicator。

### 7. Tool Node 创建 pending

- **标准答案**：Tool Node 先做批次规则、参数验证和 Policy；合法 patch 由 Executor 构建 Proposal，写入 `pending_approval`，路由到 Approval Node，且中断前不执行工具写入。
- **真实源码位置**：`src/repopilot/agent/nodes.py::ToolNode`；`src/repopilot/tools/executor.py::SafeToolExecutor`；`src/repopilot/agent/routing.py::route_after_tools`。
- **常见错误回答**：“先回一个 ToolMessage 告诉模型已提交，再去审批。”恢复后会产生第二个结果，破坏一个 tool call 对一个 ToolMessage 的协议。
- **面试表达**：pending Proposal 取代了中间 ToolMessage；只有批准或拒绝形成最终工具结果。
- **追问 1 答案**：成功消息会让模型误以为修改已完成，而且之后无法为同一调用稳定返回批准、拒绝或 stale 结果。
- **追问 2 答案**：P4 没有多操作事务和部分执行语义；整批按原 ID 返回 `approval_batch_not_supported`，保证没有读取或写入被偷偷执行。

### 8. Approval Node 的载荷与恢复验证

- **标准答案**：节点从 pending Proposal 构造安全 `ApprovalRequestView`，调用唯一一次 `interrupt()`；恢复后用预期 proposal_id 验证决定，未知结构、错误 ID 或未知决定均 fail closed。
- **真实源码位置**：`src/repopilot/agent/nodes.py::ApprovalNode`；`src/repopilot/approval/contracts.py::approval_view`；`src/repopilot/approval/validation.py::validate_resume_decision`。
- **常见错误回答**：“resume 返回 truthy 就批准。”这会把任意非空对象当授权。
- **面试表达**：中断载荷与内部应用载荷分型，恢复值再按 pending proposal 做绑定校验。
- **追问 1 答案**：完整 Proposal 含 proposed_content，API 只需 diff、哈希和统计；安全视图减少原文/新内容的重复泄漏。
- **追问 2 答案**：安全决策必须 fail closed；不确定的输入不能产生副作用，转换成稳定拒绝后仍能完成 ToolMessage 协议。

### 9. Apply/Reject 与 ToolMessage

- **标准答案**：批准路由到 ApplyPatch Node，拒绝路由到 RejectPatch Node；两者最终都调用统一 resolution 逻辑，生成恰好一个与原 tool_call_id 配对的 ToolMessage 和一条脱敏审计记录，然后清空 pending。
- **真实源码位置**：`src/repopilot/agent/nodes.py::ApplyPatchNode`、`RejectPatchNode`、`_patch_resolution_update`。
- **常见错误回答**：“拒绝不是工具执行，所以不需要 ToolMessage。”下一轮模型会看到悬空的 tool call。
- **面试表达**：审批改变的是工具结果，不改变 Tool Calling 协议；每个调用最终必须有同 ID 的结果。
- **追问 1 答案**：来自 Proposal 保存的原始 `tool_call_id`，不是审批 ID，也不是新生成 UUID。
- **追问 2 答案**：模型需要稳定获知失败才能总结或调整；错误 ToolMessage 同样完成调用配对，且不暴露堆栈或完整内容。

### 10. resume 的身份与防重放

- **标准答案**：服务先以 run_id 读取同一 checkpointer 的 snapshot，校验 run 存在、尚未完成、确有 pending、proposal_id 匹配，再用同 thread 执行 `Command(resume=...)`。
- **真实源码位置**：`src/repopilot/services/agent_service.py::AgentService.resume_run`、`_resume_run_locked`。
- **常见错误回答**：“路由层校验 proposal_id 后直接 resume。”HTTP 校验不能替代对 checkpoint 中真实 pending 的复核。
- **面试表达**：恢复前把身份、生命周期和审批对象三层都绑定到 checkpoint 状态。
- **追问 1 答案**：服务为每个 run 使用 `asyncio.Lock`；一个请求完成后，另一个重新读取状态并得到 `run_already_completed`，Applicator 只被调用一次。
- **追问 2 答案**：若锁只包住 resume 而不包状态检查，两个请求仍可能同时通过旧 snapshot，形成典型的 check-then-act 竞态。

## 三、设计取舍题答案

### 11. InMemorySaver 与 SQLite

- **标准答案**：P4 目标是证明中断、批准和应用语义，`InMemorySaver` 足够且不引入数据库；持久化和重启恢复明确留到 P6。
- **真实源码位置**：`src/repopilot/services/agent_service.py::AgentService.__init__`；`src/repopilot/agent/graph.py::build_agent_graph`。
- **常见错误回答**：“内存更快，所以生产也应该一直用。”这里是阶段边界，不是完整生产结论。
- **面试表达**：先验证状态机协议，再在 P6 替换持久化介质，避免把数据库问题混入 P4。
- **追问 1 答案**：进程重启、换 saver 或跨进程后旧 pending run 不可恢复。
- **追问 2 答案**：checkpoint 只保存过去状态；工作区文件、链接和权限属于外部现实，恢复时可能已经变化，必须再次验证。

### 12. 完整新内容与 diff hunk

- **标准答案**：完整新内容让 Proposal 生成和 Apply 都确定：系统自己计算 diff，批准后按目标哈希替换，不需要实现模糊 hunk 匹配。
- **真实源码位置**：`src/repopilot/tools/contracts.py::ProposePatchInput`；`src/repopilot/patching/proposal.py::_unified_diff`。
- **常见错误回答**：“模型输出 diff 更省 token，所以一定更安全。”解析和应用不完整/漂移 hunk 会引入另一套复杂错误面。
- **面试表达**：P4 用容量换确定性，接受单文件完整内容，同时用硬上限约束上下文和审批负载。
- **追问 1 答案**：大文件会增加模型、checkpoint 和 diff 的体积，所以 P4 设置字符与修改行数限制。
- **追问 2 答案**：集中 `TOOL_LIMITS` 同时限制原文、新内容、完整 diff、修改行数、理由和审批评论，超限直接拒绝 Proposal。

### 13. 单 patch 批次

- **标准答案**：P4 只支持“一次模型消息中的唯一 patch 调用”，避免部分只读调用已执行、patch 又挂起，或多个 patch 需要组合审批与回滚。
- **真实源码位置**：`src/repopilot/agent/nodes.py::ToolNode` 的 patch batch 检查。
- **常见错误回答**：“先执行读取，再暂停 patch 即可。”这改变严格调用顺序，也让恢复后的结果集合难以定义。
- **面试表达**：我对已支持的只读批次保持全量顺序执行；一旦出现副作用提案，就进入更严格的单操作协议。
- **追问 1 答案**：一般规则仍适用于全只读调用；含 patch 的批次属于 P4 明确不支持的输入，所有调用按原 ID 返回结构化失败，而不是部分执行。
- **追问 2 答案**：需要组合 Proposal、逐文件 stale 校验、全有或全无策略、失败清理/回滚、审批展示和幂等恢复语义。

### 14. replace 但不回滚

- **标准答案**：同目录临时文件加 replace 能避免多数部分写入风险，复杂度适合 P4；备份、跨文件事务和自动回滚会扩大到后续可靠性设计。
- **真实源码位置**：`src/repopilot/patching/applicator.py::PatchApplicator.apply`。
- **常见错误回答**：“有 `os.replace` 就绝不会失败或丢数据。”replace 前后仍可能有权限、磁盘、验证和外部竞争问题。
- **面试表达**：P4 提供单文件原子切换，不冒充数据库事务；能力边界在文档和错误码里明确。
- **追问 1 答案**：直接覆盖可能先截断目标，随后编码、磁盘或进程失败会留下不完整文件。
- **追问 2 答案**：能返回 `patch_verification_failed` 并停止继续执行；不能自动恢复原文件，因为 P4 没有持久备份/回滚承诺。

### 15. API 审批视图

- **标准答案**：完整 diff 是用户做决定的核心材料；完整 proposed content 已隐含在 diff 和 checkpoint 内部，不应在响应中再次暴露。响应仅提供相对路径、理由、哈希、统计和 ID。
- **真实源码位置**：`src/repopilot/approval/contracts.py::ApprovalRequestView`、`approval_view`；`src/repopilot/schemas/agent.py::AgentRunResult`。
- **常见错误回答**：“把整个 AgentState 返回，前端自己选字段。”这会泄漏消息历史、系统提示和内部内容。
- **面试表达**：API 使用白名单 Schema 投影，而不是序列化内部状态。
- **追问 1 答案**：API key、带敏感查询参数的 Base URL、绝对 workspace、系统提示、完整消息历史、异常堆栈和无限长工具输出。
- **追问 2 答案**：应拒绝创建 Proposal；截断会让用户批准的不是完整改动，破坏审批完整性。

## 四、安全与排错题答案

### 16. 审批期间外部修改

- **标准答案**：Applicator 重新 resolve 路径并读取当前文件，将当前 SHA-256 与 Proposal 的 original hash 对比；不一致则不写入。
- **真实源码位置**：`src/repopilot/patching/applicator.py::PatchApplicator.apply`。
- **常见错误回答**：“用户已批准，所以强制覆盖最新文件。”批准的是旧基线上的具体 diff，不是未来任意覆盖权限。
- **面试表达**：审批有上下文条件，original hash 就是乐观并发控制版本号。
- **追问 1 答案**：返回 `stale_patch`，阶段为 apply，分类为 conflict。
- **追问 2 答案**：不应；需要模型基于新内容重新生成 Proposal，并让用户重新审查和批准。

### 17. 错误或伪造决定

- **标准答案**：Pydantic 决定 Schema 拒绝额外字段、未知 decision 和过长 comment；服务层再将 proposal_id 与 checkpoint pending 绑定。客户端没有提交 path、diff 或内容的入口。
- **真实源码位置**：`src/repopilot/approval/contracts.py::ApprovalDecisionRequest`；`src/repopilot/services/agent_service.py::_resume_run_locked`；`src/repopilot/api/routes/agent.py::decide_agent_run`。
- **常见错误回答**：“只要 run_id 对，就采用请求体里的新内容。”这等于绕过原 Proposal 和人工审查。
- **面试表达**：Schema 控制形状，服务控制对象身份，Applicator 控制最终完整性，三层职责不同。
- **追问 1 答案**：额外字段/枚举/长度由 422 Schema 拦截；run 不存在、已完成、无 pending、proposal mismatch 由服务返回稳定 404/409。
- **追问 2 答案**：决定请求只表达授权，若允许重传改动材料，批准动作就能偷偷替换被批准对象。

### 18. 重复审批

- **标准答案**：顺序重复时 snapshot 已无下一节点，返回 `run_already_completed`；并发重复由 run 级异步锁串行化完整检查与恢复，第二个请求醒来后重新检查。
- **真实源码位置**：`src/repopilot/services/agent_service.py::resume_run`、`_resume_run_locked`；回归测试 `tests/integration/test_patch_approval_graph.py::test_concurrent_duplicate_approval_applies_patch_exactly_once`。
- **常见错误回答**：“`os.replace` 是幂等的，所以重复执行没问题。”重复执行可能覆盖新的外部修改，也会产生重复审计和协议结果。
- **面试表达**：我把幂等控制放在工作流恢复入口，而不是赌副作用恰好无害。
- **追问 1 答案**：顺序重复由 checkpoint 生命周期检查阻止；同进程同事件循环的并发重复由 run 级锁阻止。
- **追问 2 答案**：不够；多进程需要持久化存储提供的并发控制、租约或原子状态转换，这属于 P6 设计范围。

### 19. 最后一轮才请求 patch

- **标准答案**：模型轮次预算已经耗尽时，Tool Node 不创建 pending、不 interrupt、不写入，而是为调用返回稳定失败并终止，确保 `max_steps` 是硬边界。
- **真实源码位置**：`src/repopilot/agent/nodes.py::ToolNode`；`src/repopilot/agent/routing.py::route_after_patch_resolution`。
- **常见错误回答**：“审批不算模型轮次，所以先批准，之后再多调用一次模型。”这会让最终结果突破用户设置的轮次预算。
- **面试表达**：副作用协议不能成为预算旁路；没有剩余模型轮次时连审批都不启动。
- **追问 1 答案**：`approval_not_started_budget_exhausted`，最终状态为 `max_steps_exceeded`。
- **追问 2 答案**：步数定义必须一致且可预测；隐式追加轮次会破坏终止证明，并可能形成模型继续提案的循环。

### 20. 重启后无法恢复

- **标准答案**：这是 P4 明确的阶段限制，因为默认 checkpointer 是进程内 `InMemorySaver`；旧 UUID 在新 saver 中没有状态，不应猜测或重建审批。
- **真实源码位置**：`src/repopilot/services/agent_service.py::AgentService.__init__`；测试 `tests/integration/test_patch_approval_graph.py::test_new_in_memory_saver_cannot_resume_old_run`。
- **常见错误回答**：“根据 run_id 和 API 请求重新生成 Proposal。”缺少原 checkpoint，无法证明与用户看到的内容相同。
- **面试表达**：P4 证明协议，P6 才承诺跨重启恢复；当前失败是可观测、稳定且 fail closed 的。
- **追问 1 答案**：决策接口返回 404 和稳定 `run_not_found`，文件保持不变。
- **追问 2 答案**：即使 checkpoint 持久化，也要重验路径、链接、当前文件哈希、Proposal 完整性、审批身份和是否已经消费，不能把旧快照当成当前事实。
