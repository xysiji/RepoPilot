# RepoPilot P0–P7 开发计划

## 1. 执行规则

开发顺序固定为 `P0 -> P1 -> P2 -> P3 -> P4 -> P5 -> P6 -> P7`。任一阶段未通过验收，不得进入下一阶段；阶段完成后只报告结果并等待用户明确指令。

每阶段必须同时完成：实现、阶段测试、学习笔记、面试题、答案、Git Diff 审查。对应文档约定：

- `docs/learning/PX-notes.md`
- `docs/interview/PX-questions.md`
- `docs/interview/answers/PX-answers.md`

面试答案必须引用该阶段真实存在的 RepoPilot 文件、类和函数；计划中的未来路径不能当作完成证据。参考资料只读取本阶段列出的文件，不重新扫描整个 `reference_materials/`。

### 1.1 里程碑

- **M1（P0–P1）**：理解模型工具调用，能在安全只读工作区上完成一轮工具闭环。
- **M2（P2–P3）**：显式 LangGraph 与代码工具边界可测，仍无源码写入。
- **M3（P4–P5）**：形成一天 Demo 的批准/拒绝/测试反馈完整闭环。
- **M4（P6）**：SQLite 恢复、上下文预算和本地 Trace 达到第一版规格。
- **M5（P7）**：可选 reviewer subgraph 教学扩展；不属于一天 Demo 关键路径。

一天 Demo 快速路径只做 P0–P5 各阶段的最小验收项，预计熟悉 Python/LangGraph 的开发者约 8–10 个专注小时；完整学习、故障练习和文档约需 30–45 小时。P6 完成后才称为满足 SQLite 持久化目标的“第一版”，P7 是扩展。

## 2. P0：项目基础、配置与模型接入

1. **解决的问题**：建立可启动、可测试的 FastAPI 组合根，验证配置和 LangChain ChatModel 能被依赖注入；不实现 Agent 或工具调用。
2. **KamaClaude 对应阶段**：S0 骨架与配置；参考 S1 Provider 边界。
3. **必读课程**：`00-project-overview.md`、`01-original-learning-plan.md`、`02-s0-architecture.md`、`03-s1-agent-loop.md` 中 LLM Provider 小节。
4. **必读源码**：`core/config.py`、`core/llm/base.py`、`core/llm/provider.py`、`core/llm/types.py`、`core/app.py`；测试 `test_config_env.py`、`test_llm_provider.py`、`test_ping_roundtrip.py`。
5. **实现功能**：把占位项目元数据对齐到 Python 3.12；实现最小 Settings/Pydantic 模型、模型工厂、FastAPI app/lifespan、`GET /health`、fake model 注入点、统一 API 错误外壳。
6. **允许修改目录**：`src/repopilot/api/`、`infrastructure/`、`schemas/`、`tests/unit/`、`tests/integration/`、本阶段 docs；必要时仅为 P0 更新 `pyproject.toml`。
7. **禁止实现**：AgentLoop/StateGraph、文件工具、Patch、审批、pytest runner、SQLite、MCP、Dify、subagent、UI。
8. **输入/输出**：输入环境配置；输出校验后的 AppSettings、可注入 ChatModel 和 health JSON。模型 smoke test 默认使用 fake，不要求真实 API。
9. **验收场景**：缺少可选 tracing key 仍能启动；非法工作区根/重试上限启动失败；health 不泄露 key；fake model 可由测试替换。
10. **必须测试**：配置默认值/环境覆盖/非法值/secret 不序列化；health 200；lifespan 清理；模型工厂未知 provider 错误。
11. **必须理解**：依赖反转、Pydantic 运行时校验、FastAPI lifespan、LangChain ChatModel 抽象、配置与 State 的边界。
12. **亲手复写**：不看答案写出 Settings 校验器和 model factory 的最小协议/注入点，再与实现对照。
13. **主动修改练习**：增加一个“模型调用超时”配置，并让非法范围测试先失败后通过。
14. **故障注入练习**：让模型工厂抛出缺 key 错误，验证 API 启动日志脱敏且 health 行为符合约定。
15. **预计代码量**：产品 80–130 行；测试 70–110 行；不含自动格式化和文档。
16. **预计学习时间**：快速实现 1–1.5 小时；完整学习 3–4 小时。
17. **面试题范围**：配置优先级、secret 生命周期、ChatModel 抽象、依赖注入、lifespan 与可测试性。

**阶段出口**：运行本阶段测试、`git diff --check`、审查 Diff，生成 P0 notes/questions/answers，然后停止。

## 3. P1：最小 Tool Calling Agent

1. **解决的问题**：亲手理解“模型请求工具 -> Python 执行 -> ToolMessage 回填 -> 模型结束”的最小闭环，仍保持只读。
2. **KamaClaude 对应阶段**：S1 Agent 第一次运行。
3. **必读课程**：`03-s1-agent-loop.md`；配合 `11-technical-highlights.md` 的 Agent Loop 部分。
4. **必读源码**：`core/loop.py`、`core/context.py`、`core/tools/base.py`、`core/tools/registry.py`、`core/tools/invocation.py`、`core/tools/builtin/read_file.py`；测试 `test_loop.py`、`test_context.py`、`test_tool_registry.py`、`test_invocation.py`、`test_read_file.py`。
5. **实现功能**：最小 one-turn/limited-turn tool-calling service；安全的 `list_files`、`read_file` 初版；LangChain ToolMessage 配对；fake model 脚本化响应。
6. **允许修改目录**：`agent/`（仅 `minimal_agent` 类职责）、`tools/`、`services/workspace_guard`、`schemas/`、相关测试和阶段 docs。
7. **禁止实现**：StateGraph、planner、Patch/写文件、通用 shell、pytest、审批、session/checkpoint、事件总线。
8. **输入/输出**：输入 user_task、已校验 workspace；输出模型最终文本、工具调用摘要和结构化错误。
9. **验收场景**：fake model 先读取一个文件再正确回答；未知工具、缺参数、文件不存在能回填错误；超过 max_tool_rounds 停止。
10. **必须测试**：消息顺序、多个 tool call 的处理策略、未知工具、Pydantic extra forbid、读取截断、absolute/`..`/外部 symlink 拒绝、轮次上限。
11. **必须理解**：tool schema、AIMessage/ToolMessage 配对、模型决定与程序执行的边界、错误作为观察反馈、ReAct 最小机制。
12. **亲手复写**：手写一次不依赖预构建 agent 的“检查 tool_calls、调用、构造 ToolMessage”关键循环。
13. **主动修改练习**：给 read_file 增加 start_line/end_line，并让模型基于行范围读取而不是整文件。
14. **故障注入练习**：fake model 重复请求同一个不存在文件，证明轮次上限会终止且错误不会变成无限循环。
15. **预计代码量**：产品 110–170 行；测试 100–150 行。
16. **预计学习时间**：快速实现 1.5–2 小时；完整学习 4–5 小时。
17. **面试题范围**：Tool Calling 协议、消息配对、Pydantic args、工具错误、轮次终止和 fake model。

**阶段出口**：最小循环是教学过渡实现；P2 会迁入显式图。完成测试/文档/Diff 后停止，不提前重构 P2。

## 4. P2：LangGraph State 与显式流程

1. **解决的问题**：把 P1 的手写 Tool Calling 循环迁移为可路由、可单测的显式 StateGraph；本阶段仍是无持久化的只读 Agent。
2. **KamaClaude 对应阶段**：S1 AgentLoop/Runner/ExecutionContext；S3 TaskManager 仅用于理解状态与终止条件，不迁移规划功能。
3. **必读课程**：`03-s1-agent-loop.md`、`05-s3-planning.md` 中与状态、路由和终止条件相关的部分。
4. **必读源码**：`core/runner.py`、`core/loop.py`、`core/context.py`、`core/task/model.py`、`core/task/manager.py`；测试 `test_runner.py`、`test_loop.py`、`test_task_model.py`、`test_task_manager.py`。
5. **实际实现**：`AgentState`、自定义 `ModelNode`/`ToolNode`、两个纯路由、graph builder 和 `AgentService.ainvoke()`；生产图为 `START -> model -> tools/model END`。
6. **允许修改目录**：`agent/`、`services/`、`api/`、P1 测试辅助与相关 tests/scripts/docs；P1 三个只读工具保持不变。
7. **禁止实现**：Planner/TaskManager、Patch、approval、run_tests、Checkpointer、SQLite、interrupt、streaming、MCP/subagent/EventBus。
8. **输入/输出**：继续使用 P1 `POST /agent/run` 与 `AgentRunResult`，不新增 thread、runs 查询或原始 State 输出。
9. **验收场景**：直接回答、单轮/多轮/同轮多工具、工具错误恢复、模型错误、空回答、模型轮次耗尽和跨请求隔离。
10. **必须测试**：State reducers、节点局部更新、纯路由、关键拓扑、完整消息协议、编译图复用、API/Health 回归和零网络调用。
11. **必须理解**：StateGraph、START/END、conditional edges、reducers、node purity、模型轮次与 recursion limit 的区别。
12. **亲手复写**：AgentState、Model Node、路由函数和 graph builder，共约 120–220 行。
13. **主动修改练习**：仅在学习分支中增加“连续两次工具失败后提前终止”，通过 State 字段和节点更新实现。
14. **故障注入练习**：破坏 reducer、路由、ToolMessage 配对、model_calls 或图编译位置，观察测试如何定位。
15. **预计代码量**：以实际职责为准，不因未来规划增加模型或节点。
16. **预计学习时间**：快速实现 2 小时；完整学习 5–6 小时。
17. **面试题范围**：图与循环、State/reducers、消息配对、纯路由、模型轮次、recursion limit、图隔离和图测试。

**阶段出口**：必须证明图仍然没有文件副作用。完成阶段材料和 Git 检查后停止。

## 5. P3：代码工具与安全边界

1. **解决的问题**：让 executor 能高效搜索真实代码，同时建立所有后续副作用都依赖的 WorkspaceGuard、固定子进程和结构化错误。
2. **KamaClaude 对应阶段**：S3 工具扩展，S5 参数校验/权限/失败分类，S6 tool_result 截断。
3. **必读课程**：`05-s3-planning.md` 工具部分、`08-s5-tool-safety.md`、`09-s6-context.md` 的 tool_result 截断。
4. **必读源码**：`core/tools/invocation.py`、`core/tools/builtin/read_file.py`、`core/tools/builtin/list_dir.py`、`core/tools/builtin/bash.py`、`core/tools/builtin/write_file.py`、`core/permissions/policy.py`；测试 `test_builtin_tools.py`、`test_read_file.py`、`test_tool_params.py`、`test_tool_retry.py`、`test_permission_policy.py`。
5. **实现功能**：完善 WorkspaceGuard；`search_code`；`read_git_diff`；固定 `run_tests` service（暂不接图循环）；输出/文件/Patch 前置限制；统一 ToolError。
6. **允许修改目录**：`tools/`、`services/`、`schemas/`、executor 工具装配、相关测试/docs。
7. **禁止实现**：通用 bash、`shell=True`、任意 pytest args、文件写入、Patch/审批、SQLite、MCP。
8. **输入/输出**：受限相对路径、搜索 query、测试目标；输出带 path/hash/line/exit_code/timeout/truncated 的结构化结果。
9. **验收场景**：搜索并读取目标函数；安全读取 Git Diff；固定 pytest 可在 fixture 项目运行；所有越界和超限输入 fail closed。
10. **必须测试**：absolute、`..`、symlink 外跳、Windows drive/UNC（平台可条件化）、NUL、deny globs、binary、max bytes/matches、subprocess 参数数组、timeout kill/reap、stdout/stderr 截断、Git 失败。
11. **必须理解**：规范化路径、TOCTOU、symlink、命令注入、subprocess 生命周期、结构化错误和副作用重试风险。
12. **亲手复写**：WorkspaceGuard 的 resolve/containment 关键函数和 run_tests 的 terminate/kill/reap 路径。
13. **主动修改练习**：把 search_code 的最大匹配数减半，修改测试证明截断仍保留确定性顺序和 omitted 计数。
14. **故障注入练习**：在工作区内创建指向外部的 symlink，证明 list/read/search 全部拒绝；再模拟 pytest 永不退出验证无孤儿进程。
15. **预计代码量**：产品 220–330 行；测试 240–360 行。
16. **预计学习时间**：快速实现 2–3 小时；完整学习 6–8 小时。
17. **面试题范围**：路径穿越、symlink/TOCTOU、shell 注入、超时回收、ToolError、输出预算。

**阶段出口**：安全测试是阻断门；任何“靠 Prompt 不越界”的实现不得进入 P4。

## 6. P4：Patch 与人工审批

1. **解决的问题**：把模型提案变成可审阅 Diff，并确保无匹配批准绝不写文件。
2. **KamaClaude 对应阶段**：S5 Permission Policy/Manager；借鉴 S3 write_file，但不迁移通用写工具。
3. **必读课程**：`08-s5-tool-safety.md`；补读 `03-s1-agent-loop.md` 的工具执行顺序。
4. **必读源码**：`core/permissions/policy.py`、`core/permissions/manager.py`、`core/permissions/storage.py`、`core/tools/invocation.py`、`core/tools/builtin/write_file.py`、`core/app.py` 的 permission handler；测试 `test_permission_manager.py`、`test_permission_policy.py`、`test_s5_permission_flow.py`。
5. **实现功能**：ChangeProposal/FileEdit、generate_patch、Patch hash、approval node interrupt、approval API、apply_patch 全量预检/原子替换/尽力回滚、批准和拒绝路由。
6. **允许修改目录**：`agent/`、`services/`、`schemas/`、`api/`、`tools/`（仅程序化 Patch 接口）、测试/docs。
7. **禁止实现**：always_allow/deny、审批时自由编辑、通用 write_file/bash、测试反馈循环、持久 SQLite（本阶段可 InMemorySaver）、UI。
8. **输入/输出**：输入结构化 FileEdit；输出 PatchResult、ApprovalRequest、ApplyPatchResult 和批准/拒绝 final report。
9. **验收场景**：运行到 interrupt 前仓库不变；批准相同 hash 后才修改；拒绝不修改；过期 hash/HEAD/preimage 被拒绝；每个新 Patch 有独立批准。
10. **必须测试**：unified diff 正确性、空/冲突/重复 old snippet、文件/字节上限、interrupt payload、同 thread resume、错误 thread/hash、重复批准、dirty tree、审批后文件变化、半失败回滚。
11. **必须理解**：HITL、interrupt 节点重放语义、幂等性、批准绑定、TOCTOU、乐观并发控制、fail closed。
12. **亲手复写**：approval 节点与 apply 前的五项复核；不得把关键批准条件交给生成式代码后不理解。
13. **主动修改练习**：给 ApprovalRequest 增加风险等级并从文件类型/变更行数确定性计算，验证不由 LLM 自报风险。
14. **故障注入练习**：interrupt 后人工改动目标文件，再提交旧 hash 批准，证明系统拒绝且不覆盖人工改动。
15. **预计代码量**：产品 200–300 行；测试 220–330 行。
16. **预计学习时间**：快速实现 2–3 小时；完整学习 6–8 小时。
17. **面试题范围**：interrupt/resume、Patch 哈希、审批重放、TOCTOU、原子写与回滚、授权粒度。

**阶段出口**：批准与拒绝两条集成路径都通过，Git Diff 只含预期文件；生成 P4 材料后停止。

## 7. P5：pytest 反馈与自动修复循环

1. **解决的问题**：把已实现的 run_tests 接入图，让测试结果驱动有限重规划，并用累计 Git Diff 生成最终报告。
2. **KamaClaude 对应阶段**：S3 Task planning、S5 失败分类/重试、S6 输出治理；原项目没有同等“Patch-测试-再审批”完整产品循环。
3. **必读课程**：`05-s3-planning.md`、`08-s5-tool-safety.md` 重试部分、`09-s6-context.md` 输出截断、`11-technical-highlights.md`。
4. **必读源码**：`core/task/manager.py`、`core/tools/invocation.py`、`core/compact/budget.py`、`core/runner.py`；测试 `test_task_manager.py`、`test_tool_retry.py`、`test_budget.py`、`test_run_e2e.py`。
5. **实现功能**：tester node、TestResult 分类、test_router、retry_count/max_retries、失败摘要注入 planner、累计 Diff reviewer、Python 锁定事实的最终报告；可由 LLM 生成说明，失败时回退确定性模板。
6. **允许修改目录**：`agent/`、`services/`、`schemas/`、`prompts/`、API state projection、相关测试/docs。
7. **禁止实现**：无限 retry、失败后自动写而不审批、自动 pip install、任意 shell、baseline 大型测试系统、LLM reviewer/subagent、SQLite。
8. **输入/输出**：输入已批准并应用的 Patch；输出 TestResult、可能的新 ApprovalRequest、ReviewResult、FinalReport。
9. **验收场景**：一次通过；第一次失败后重规划并再次审批、第二次通过；重试耗尽；collection/timeout 被判不可自动修复；拒绝第二次 Patch 后保留清晰报告。
10. **必须测试**：三路 router、retry off-by-one、每轮新 patch_hash、失败摘要限长、不可重试错误、累计 Diff、冲突标记/越界文件审查、最终报告字段完整。
11. **必须理解**：业务重试与基础设施重试、测试 oracle、replan vs retry、循环终止、累计/增量 Diff、自动修复的停止条件。
12. **亲手复写**：test_router 和错误分类映射；用表驱动测试覆盖所有 `(result,retry_count)` 组合。
13. **主动修改练习**：把默认 max_retries 从 1 改为 0，通过测试证明系统退化为“一次修改、失败即报告”而不是路由错误。
14. **故障注入练习**：让 pytest 返回 collection error 或超时，证明不会把环境问题当成代码断言失败反复改源码。
15. **预计代码量**：产品 190–280 行；测试 210–320 行。
16. **预计学习时间**：快速实现 2–3 小时；完整学习 6–8 小时。
17. **面试题范围**：自动修复循环、测试反馈、重试预算、错误归因、Diff reviewer、最终报告可解释性。

**阶段出口**：P5 形成一天 Demo。只演示和报告，不自动进入持久化阶段。

## 8. P6：Session、SQLite、上下文与 Trace

1. **解决的问题**：让审批和 run 在进程重启后可恢复，限制长期上下文，并提供不依赖云服务的执行 Trace。
2. **KamaClaude 对应阶段**：Trace、S4 Session/Memory、S6 Context Compact。
3. **必读课程**：`06-s3-trace.md`、`07-s4-session-memory.md`、`09-s6-context.md`。
4. **必读源码**：`core/trace/record.py`、`core/trace/writer.py`、`core/trace/provider.py`、`core/session/model.py`、`core/session/store.py`、`core/session/manager.py`、`core/memory/loader.py`、`core/compact/budget.py`、`core/compact/compactor.py`；对应 trace/session/budget/compactor 测试。
5. **实现功能**：AsyncSqliteSaver、稳定 thread_id、恢复 API/锁、TraceEvent reducer、context budget、限长失败摘要、可选 LangSmith 开关；不必默认启用 LLM compact。
6. **允许修改目录**：`infrastructure/`、`agent/`、`schemas/`、`services/context`、`api/`、测试/docs；必要时更新 P6 依赖声明。
7. **禁止实现**：自建 SessionStore/thread.jsonl/EventWriter、向量库、跨用户长期 memory、默认上传私有代码、队列/分布式锁、自动有损摘要。
8. **输入/输出**：输入 thread_id/恢复命令；输出 checkpoint state projection、节点 trace、可恢复 interrupt 和最终报告 trace 摘要。
9. **验收场景**：在 approval 中断后关闭并重建 app，用同 thread_id 恢复；不同 thread 隔离；并发重复 resume 被拒；trace 无 secret/完整源码。
10. **必须测试**：SQLite roundtrip、restart resume、thread isolation、状态 serialization、同 thread 并发锁、trace append、payload redaction、context/result limit、损坏/缺失 checkpoint 错误。
11. **必须理解**：checkpoint/thread/session 区别、durable execution、SQLite 并发、状态序列化、trace/log/checkpoint 区别、上下文有损治理。
12. **亲手复写**：checkpointer composition 与恢复时的外部状态复核；TraceEvent 脱敏函数。
13. **主动修改练习**：为 trace 增加按 node 统计耗时的 report 字段，证明统计来自结构化事件而非日志字符串解析。
14. **故障注入练习**：中断后改变 Git HEAD 或删除工作区，再恢复 checkpoint，证明恢复不等于继续写入。
15. **预计代码量**：产品 170–260 行；测试 190–280 行。
16. **预计学习时间**：快速实现 2–3 小时；完整学习 6–8 小时。
17. **面试题范围**：SQLite checkpointer、durable execution、thread_id、恢复安全、Trace 脱敏、上下文预算。

**阶段出口**：P6 后第一版规格完成。运行全量测试、检查 Git Diff、生成材料，然后停止。

## 9. P7：只读 Reviewer Subgraph

1. **解决的问题**：在不引入并行多 Agent 的前提下，把 reviewer 与 executor 的上下文和工具权限隔离，学习 LangGraph subgraph。
2. **KamaClaude 对应阶段**：S7 Skills、Subagents 与 MCP 中的 planner/executor/reviewer 角色；本阶段只取 reviewer。
3. **必读课程**：`10-s7-extension.md`、`11-technical-highlights.md` 多 Agent 部分、`12-original-interview-questions.md`。
4. **必读源码**：`core/agents/loader.py`、`core/agents/builtin/reviewer.toml`、`core/subagent/registry.py`、`core/subagent/tool.py`、`core/skills/builtin/orchestrate.md`；测试 `test_agent_profile_loader.py`、`test_spawn_agent_tool.py`。
5. **实现功能**：一个 per-invocation reviewer subgraph；显式父/子 State 映射；仅可读取累计 Diff、计划和测试结果；输出结构化 ReviewResult；可配置回退到 P5 deterministic reviewer。
6. **允许修改目录**：`agent/reviewer`、`schemas/`、`prompts/`、graph composition、相关测试/docs。
7. **禁止实现**：并行/后台 subagents、通用 spawn_agent/agent_result、递归嵌套、executor 子 Agent、MCP、Dify、Skills loader、额外写权限。
8. **输入/输出**：输入只读 ReviewInput 投影；输出 ReviewResult，不共享父 messages，不修改 workspace。
9. **验收场景**：reviewer 能指出测试失败/越界 Diff/冲突标记；模型失败时回退确定性 reviewer；工具列表不含写/测试能力。
10. **必须测试**：父子 State 映射、冷上下文、tool whitelist、per-invocation persistence、模型失败回退、无 workspace 修改、输出 schema 校验。
11. **必须理解**：subgraph、per-invocation/per-thread persistence、上下文隔离、能力最小化、模型审查与确定性审查互补。
12. **亲手复写**：父 State -> ReviewInput -> ReviewResult -> 父 State 的适配函数和只读 registry 装配。
13. **主动修改练习**：让 reviewer 只接收 Diff 摘要，再改为按需 read_git_diff，比较 token 与证据质量。
14. **故障注入练习**：故意把 apply_patch 加入 reviewer 工具列表，让安全测试失败；移除后证明编译/测试恢复。
15. **预计代码量**：产品 130–210 行；测试 150–230 行。
16. **预计学习时间**：快速实现 2 小时；完整学习 5–7 小时。
17. **面试题范围**：subgraph 状态映射、上下文隔离、权限最小化、reviewer 幻觉、回退策略、多 Agent 成本。

**阶段出口**：P7 只完成 reviewer subgraph。MCP 与 Dify 继续留在后续决策，不得顺手实现。

## 10. 全局测试与质量门

每阶段最少执行本阶段 focused tests；P2 起运行现有全部 unit tests；P4 起增加临时 Git 仓库集成测试；P5/P6/P7 运行全量测试。真实模型测试单独用 `integration` 标记且默认跳过，CI 和核心验收依赖脚本化 fake model，避免网络波动。

阶段结束共同门槛：

1. 用户场景与至少一个失败场景可重复演示。
2. 所有安全不变量有代码断言和测试，不只存在于 prompt/docstring。
3. 没有超出本阶段允许目录的改动。
4. `pytest`、`git diff --check` 通过；人工检查 Git Diff 无参考资料泄露。
5. 阶段学习笔记、20 个面试题主题和真实源码答案已生成。
6. 不自动创建下一阶段文件或依赖。
