# RepoPilot P0–P7 学习计划

## 1. 学习方法

每阶段使用同一闭环：先提出问题，再按 `REFERENCE_INDEX.md` 定向阅读课程和源码；先运行参考测试理解行为，再在 RepoPilot 亲手复写关键机制；随后主动改变一个约束、制造一个故障，用测试和 Git Diff 证明理解。禁止把参考源码整段复制后再倒推原理。

每阶段学习产物写入 `docs/learning/PX-notes.md`，最少包含：

- 本阶段要解决的一个核心问题和非目标。
- 一张基于真实实现的调用链或状态表。
- 三项关键不变量及其测试位置。
- 一次主动修改和一次故障注入的前后证据。
- 与 KamaClaude 的一个相同点、一个差异和差异原因。
- 能在 3 分钟内口述的阶段总结。

运行参考项目只为观察指定行为，不修改 `reference_materials/kamaclaude`。运行 RepoPilot 前先确认当前阶段范围；阶段完成后生成笔记和面试材料并停止。

## 2. P0：基础、配置与模型接入

- **看什么**：`00-project-overview.md`、`01-original-learning-plan.md`、`02-s0-architecture.md`；了解为何先固定边界和契约。
- **运行什么**：只运行 KamaClaude 的 `test_config_env.py`、`test_llm_provider.py` 中 fake/client 注入相关用例；RepoPilot 运行配置和 health focused tests。
- **精读代码**：`core/config.py:get_config/_apply_env`、`core/llm/base.py:LLMProvider`、`core/llm/provider.py:AnthropicProvider.__init__/chat`、`core/app.py:CoreApp.run`。重点区分组合、配置与调用。
- **亲手复写**：关闭参考文件后写 Settings 校验、model factory 和 fake model 注入点；能解释每一字段为何存在。
- **主动修改**：新增模型超时设置，从 schema、环境覆盖到测试完整贯通；不要增加第二个无需求 Provider。
- **制造错误**：缺 key、未知 provider、非法 retry/timeout、secret 被误序列化；观察启动失败点和对外错误。
- **证明掌握**：测试能在无网络、无真实 key 下稳定通过；health 无敏感字段；画出 FastAPI -> dependency -> model factory 的调用关系。
- **面试表达**：用“配置在边界一次校验，模型通过协议注入，业务状态不携带 secret”概括；能回答为何不复制 AnthropicProvider。

## 3. P1：最小 Tool Calling

- **看什么**：`03-s1-agent-loop.md` 的 ExecutionContext、AgentLoop、ToolRegistry 和 tool_result 顺序。
- **运行什么**：参考 `test_loop.py`、`test_context.py`、`test_invocation.py`、`test_read_file.py`；RepoPilot 运行 fake model 的读文件闭环。
- **精读代码**：`core/loop.py:AgentLoop.run`、`core/context.py:add_tool_result`、`core/tools/invocation.py:invoke_tool`、`core/tools/builtin/read_file.py:invoke`。
- **亲手复写**：用 LangChain 标准消息手写一次“AIMessage.tool_calls -> 工具 -> ToolMessage -> 再调用模型”，不使用预构建 agent 掩盖协议。
- **主动修改**：给 read_file 加行范围读取，比较完整文件与 focused read 的 token/证据差异。
- **制造错误**：未知工具、缺 path、外部 symlink、模型永远要求同一工具；验证结构化错误和 max_tool_rounds。
- **证明掌握**：能在纸上写出消息顺序；测试断言每个 tool_call_id 配对；工作区外文件即使存在也读不到。
- **面试表达**：强调模型只“提出工具调用”，Python 才执行；工具错误作为观察反馈，终止条件由代码控制。

## 4. P2：LangGraph State 与流程

- **看什么**：`03-s1-agent-loop.md`、`05-s3-planning.md`；再看 LangGraph StateGraph、conditional edges、reducers 官方文档。
- **运行什么**：参考 `test_runner.py`、`test_loop.py`、`test_task_manager.py`；RepoPilot 运行每个 node 单测和分析型 graph 集成测试。
- **精读代码**：`core/runner.py:AgentRunner.run_and_capture/_build_registry`、`core/loop.py:run`、`core/context.py:ExecutionContext`、`core/task/manager.py:create/update`。
- **亲手复写**：先画 State writer/reader 表，再写 graph builder；独立写 router 的表驱动测试，不把所有状态塞 messages。
- **主动修改**：向 ExecutionPlan 增加 acceptance criteria，并让 executor、reviewer/report 通过 State 读取。
- **制造错误**：删除一条 END 边、让 reducer 配置错误、让 plan 返回非法步骤；观察图挂起/校验/路由表现。
- **证明掌握**：能从任意节点说明下一边和终止条件；State 表与真实字段一致；图中没有写文件能力。
- **面试表达**：对比手写 while 和显式图：图增加状态可见、条件路由、checkpoint/interrupt 接入点，但也增加 schema 和路由复杂度。

## 5. P3：代码工具与安全边界

- **看什么**：`05-s3-planning.md` 工具、`08-s5-tool-safety.md` 参数/权限/重试、`09-s6-context.md` tool_result 截断。
- **运行什么**：参考 `test_builtin_tools.py`、`test_read_file.py`、`test_tool_params.py`、`test_tool_retry.py`、`test_permission_policy.py`；RepoPilot 运行完整 security test matrix。
- **精读代码**：`core/tools/builtin/read_file.py`、`core/tools/builtin/list_dir.py`、`core/tools/builtin/bash.py`、`core/tools/builtin/write_file.py`、`core/permissions/policy.py:evaluate/matches_outside_cwd`、`core/tools/invocation.py`。
- **亲手复写**：WorkspaceGuard 的 canonicalization/containment/symlink 复核，以及固定 pytest 子进程的 timeout/terminate/kill/reap。
- **主动修改**：改变 search max_matches 或 read max_bytes，更新结果模型和边界测试，观察模型上下文变化。
- **制造错误**：absolute path、`..`、外部 symlink、Windows UNC/盘符、二进制、大输出、超时 pytest、Git 命令失败。
- **证明掌握**：安全矩阵每格有测试；生产代码无 `shell=True`；模型可见工具列表不含写/测试；错误 code 可稳定断言。
- **面试表达**：指出 KamaClaude 的 `..` 检查为何不足；说明路径边界、进程边界和输出边界是三种不同防线。

## 6. P4：Patch 与人工审批

- **看什么**：`08-s5-tool-safety.md` 的 PermissionManager/Future/审批链，再读 LangGraph interrupts 的重放规则。
- **运行什么**：参考 `test_permission_policy.py`、`test_permission_manager.py`、`test_s5_permission_flow.py`；RepoPilot 跑 approve/reject/stale/replay 集成测试。
- **精读代码**：`core/permissions/manager.py:check_and_wait/respond`、`core/permissions/policy.py:evaluate`、`core/tools/invocation.py` 的权限位置、`core/tools/builtin/write_file.py`。
- **亲手复写**：Patch hash 计算、approval interrupt payload、恢复校验、apply 前 HEAD/worktree/preimage 五项复核。
- **主动修改**：增加确定性风险等级或 Patch 文件数上限，让审批载荷和拒绝原因同步变化。
- **制造错误**：审批等待时修改文件/HEAD、重复 resume、提交其他 thread/hash、让第二个文件写失败。
- **证明掌握**：审批前/拒绝后仓库 hash 不变；旧批准无法授权新 Patch；失败回滚和错误报告可重复。
- **面试表达**：用“批准绑定 Patch 内容与外部世界快照，而不是绑定工具名”概括；解释 interrupt 前不能有非幂等副作用。

## 7. P5：pytest 反馈与自动修复

- **看什么**：`05-s3-planning.md`、`08-s5-tool-safety.md` 重试、`09-s6-context.md` 截断、`11-technical-highlights.md`。
- **运行什么**：参考 `test_tool_retry.py`、`test_budget.py`、`test_run_e2e.py`；RepoPilot 跑 pass/retry/exhausted/environment-error 四类集成场景。
- **精读代码**：`core/tools/invocation.py` 的 retryable 分类、`core/task/manager.py` 状态、`core/compact/budget.py`、`core/runner.py` 的 final outcome。
- **亲手复写**：test_router 表驱动逻辑、TestResult 分类、失败摘要生成和 cumulative Diff reviewer。
- **主动修改**：把 max_retries 改为 0/2，证明边数、审批次数、最终报告和 off-by-one 都正确。
- **制造错误**：assertion fail、collection error、timeout、测试工具内部异常、第一次修复失败后第二次 Patch 被拒绝。
- **证明掌握**：每次增量 Patch 都有新 hash/审批；不可修复环境错误不循环；最终报告能解释为什么停。
- **面试表达**：区分网络重试、工具重试和业务重规划；说明 pytest 是不完美 oracle，重试预算是安全/成本边界。

## 8. P6：SQLite、上下文与 Trace

- **看什么**：`06-s3-trace.md`、`07-s4-session-memory.md`、`09-s6-context.md`；LangGraph persistence 与 LangSmith observability 官方文档。
- **运行什么**：参考 trace/session/budget/compactor 单测；RepoPilot 做“审批中断 -> 重建 app -> 恢复”的真实 SQLite 测试。
- **精读代码**：`core/session/store.py:read_messages/_trim_orphan_tool_use`、`core/session/manager.py:send_message`、`core/trace/writer.py`、`core/trace/provider.py`、`core/compact/budget.py`、`core/compact/compactor.py`。
- **亲手复写**：checkpointer 注入、thread_id 使用、恢复时外部状态复核、TraceEvent 脱敏和 context budget。
- **主动修改**：增加节点耗时统计或 trace sampling 配置，验证 final report 使用结构化事件。
- **制造错误**：损坏/缺失 checkpoint、同 thread 并发恢复、恢复时 workspace 消失/HEAD 改变、trace 中注入 fake secret。
- **证明掌握**：重启恢复成功且 stale workspace fail closed；不同 thread 隔离；SQLite/trace 文件不在目标仓库；脱敏测试通过。
- **面试表达**：区分 session、thread、checkpoint、trace 和 memory；说明 SQLite 适合本地但不适合多进程高并发。

## 9. P7：Reviewer Subgraph

- **看什么**：`10-s7-extension.md` 的 role/tool whitelist/subagent 隔离；LangGraph subgraph persistence 官方文档。
- **运行什么**：参考 `test_agent_profile_loader.py`、`test_spawn_agent_tool.py`；RepoPilot 运行 reviewer 父子 State 映射和只读能力测试。
- **精读代码**：`core/agents/loader.py`、`core/agents/builtin/reviewer.toml`、`core/subagent/tool.py:invoke/_build_child_registry`、`core/skills/builtin/orchestrate.md`。
- **亲手复写**：父 State 到 ReviewInput 的最小投影、per-invocation subgraph、ReviewResult 回写和 deterministic fallback。
- **主动修改**：比较“直接给完整 Diff”和“reviewer 按需读取 Diff”的 token、证据和复杂度。
- **制造错误**：给 reviewer 错配 apply_patch、让 reviewer 输出非法 schema、模型超时、错误设置 per-thread persistence。
- **证明掌握**：写工具在编译/装配测试中不可见；reviewer 失败不阻塞确定性 final report；父 messages 不泄漏给子图。
- **面试表达**：说明为什么这里只做串行只读 subgraph，不做后台 spawn；能比较 multi-agent 收益、成本和权限面。

## 10. 掌握度检查

每阶段用四级标准自评：

1. **能复述**：能说明概念和主链，但离开代码不能定位。
2. **能定位**：能在 2 分钟内指出真实文件/类/函数和对应测试。
3. **能修改**：能完成主动修改并解释受影响的 State、边和测试。
4. **能排错**：面对故障注入能先提出假设，再用 trace/test/diff 证伪，最后说明取舍。

只有达到第 3 级且安全相关阶段达到第 4 级，才视为掌握。面试答案不得只背课程术语，必须引用 RepoPilot 当阶段的真实实现和一次失败证据。
