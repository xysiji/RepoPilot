# KamaClaude → RepoPilot 迁移图

## 1. 判定原则

迁移不是按目录复制，而是先判断原模块解决的问题在 RepoPilot 的“一天可运行 Demo”中是否仍存在。框架已经稳定提供的通用运行时能力优先交给 LangChain/LangGraph；与工作区安全和代码修改语义直接相关的边界必须由 RepoPilot 自己实现并测试。

判定枚举：`直接迁移`、`修改后迁移`、`使用 LangChain/LangGraph 替换`、`第一版删除`、`后续阶段再实现`。当前没有计划原样复制完整源码；“直接迁移”只会在未来完成许可证核对、接口适配和测试后使用。

## 2. 总览

| # | KamaClaude 模块 | RepoPilot 计划对应 | 迁移方式 | 框架替代 | 第一版 |
| --- | --- | --- | --- | --- | --- |
| 1 | 配置管理 | `infrastructure/config.py` | 修改后迁移 | Pydantic v2 | 保留 |
| 2 | LLM Provider | `infrastructure/model_factory.py` | 使用框架替换 | LangChain ChatModel | 保留 |
| 3 | AgentRunner | `agent/graph.py` + `services/agent_service.py` | 使用框架替换 | LangGraph compiled graph | P2 已保留组合职责，不保留巨型类 |
| 4 | AgentLoop | `agent/nodes.py` + `agent/routing.py` | 使用框架替换 | LangGraph StateGraph | P2 已替换手写循环 |
| 5 | ExecutionContext | `agent/state.py` | 使用框架替换 | LangGraph State/reducers | P2 已实现最小显式状态 |
| 6 | ToolRegistry | `tools/readonly.py` + 自定义 ToolNode | 修改后迁移 | LangChain BaseTool；不采用预构建 ToolNode | P2 保留顺序和审计语义 |
| 7 | 文件读取工具 | `tools/read_file.py` | 修改后迁移 | LangChain tool schema | 保留 |
| 8 | 文件写入工具 | `patching/proposal.py` + `patching/applicator.py` | 第一版删除通用工具；独立实现专用能力 | interrupt 仅负责暂停 | P4 已保留单文件受批写入 |
| 9 | Bash 工具 | 固定 `run_tests`/Git 服务 | 第一版删除 | 无 | 不保留通用 shell |
| 10 | TaskManager | 后续 State 中的 `ExecutionPlan` | 后续阶段再实现 | LangGraph State/checkpoint | P2 不迁移 |
| 11 | EventBus | graph updates/trace state | 第一版删除 | LangGraph stream/callback | 不保留 |
| 12 | EventWriter | checkpoint + trace_events | 第一版删除 | LangGraph persistence | 不保留 JSONL writer |
| 13 | TraceWriter | P6 trace adapter | 后续阶段再实现 | LangSmith 可选 + State trace | 部分保留 |
| 14 | Permission Policy | `tools/policy.py` | 修改后迁移 | 无 | P4 已实现三态专用策略 |
| 15 | PermissionManager | approval node/API | 修改后迁移 | LangGraph interrupt | P4 已替换 Future 等待 |
| 16 | Session Store | SQLite Checkpointer | 使用框架替换 | LangGraph persistence | P6 保留 |
| 17 | Notes/Context | 项目规则与上下文装配 | 后续阶段再实现 | prompt/context 管理 | P6 最小保留 |
| 18 | Context Compact | 预算与摘要策略 | 后续阶段再实现 | LangChain message utilities | P6 最小保留 |
| 19 | Skills | 可选工作流模板 | 后续阶段再实现 | 可组合 graph/subgraph | 非 Demo |
| 20 | Subagents | reviewer subgraph | 后续阶段再实现 | LangGraph subgraph | P7 可选 |
| 21 | MCP Client | MCP adapter | 使用框架替换，P7 之后再评估 | `langchain-mcp-adapters` | 非 Demo |
| 22 | daemon | 单 FastAPI 进程 | 第一版删除 | ASGI server 生命周期 | 不保留 |
| 23 | IPC | HTTP API | 第一版删除 | FastAPI/Pydantic | 不保留 |
| 24 | CLI | HTTP 调试客户端 | 第一版删除 | OpenAPI/curl | 不保留正式 CLI |
| 25 | TUI | 无 | 第一版删除 | 无 | 不保留 |

## 3. 逐模块分析

### 1. 配置管理

- **原项目解决的问题**：`src/kama_claude/core/config.py` 用 dataclass、TOML、`.env` 和环境变量组织 daemon、LLM、trace、权限、compact、MCP 配置；`tests/unit/test_config_env.py` 验证优先级。
- **新项目仍有的问题**：需要模型名、API key 引用、允许工作区根、pytest 超时、重试上限、SQLite 路径和可选 tracing 开关。
- **处理方式**：**修改后迁移**。只保留第一版真实需要的少量设置，启动时一次校验；敏感值只从环境读取。
- **复用代码或设计**：复用“默认值 < 文件/`.env` < 系统环境”的概念和针对优先级的测试思路，不复制 KamaConfig 大模型。
- **框架能力**：Pydantic v2 负责运行时校验；不因未来需求提前引入复杂配置中心。
- **放弃原因**：原配置包含 host/port daemon、trace 文件、MCP server 和 Textual 相关范围，复制会带入被删除架构。
- **失去能力**：暂不支持用户级 TOML 多层覆盖、daemon 端口和 MCP 配置。
- **面试追问**：配置优先级如何证明？为什么 API key 不进入 State/checkpoint？配置变更后运行中的 graph 如何处理？

### 2. LLM Provider

- **原项目解决的问题**：`src/kama_claude/core/llm/base.py`、`src/kama_claude/core/llm/provider.py`、`src/kama_claude/core/llm/types.py` 封装 Anthropic 流式响应、tool_use、usage、缓存和重试；测试在 `test_llm_provider.py`。
- **新项目仍有的问题**：需要可替换模型、工具绑定、结构化输出、调用错误和测试 fake。
- **处理方式**：**使用 LangChain/LangGraph 替换**，通过模型工厂返回 LangChain `BaseChatModel`。
- **复用代码或设计**：复用 Provider 边界、依赖注入、Usage/错误可观测和网络重试需区分的设计，不复用 Anthropic 消息解析代码。
- **框架能力**：ChatModel、`bind_tools()`、`with_structured_output()`、标准消息与 callbacks。
- **放弃原因**：原实现强绑定 Anthropic SDK 和 content block 顺序，LangChain 已提供跨 Provider 适配。
- **失去能力**：第一版不保证原生 extended-thinking block、Anthropic prompt caching 细节和逐 token 自定义事件完全等价。
- **面试追问**：抽象层如何避免最低公分母？结构化输出失败如何恢复？框架重试与业务重试为何要分开？

### 3. AgentRunner

- **原项目解决的问题**：`src/kama_claude/core/runner.py` 是组合根，创建 provider、registry、loop、task/session/trace/permission/MCP 并管理 run 生命周期；`test_runner.py` 验证首尾事件与注入。
- **新项目仍有的问题**：仍需组合依赖、建立一次 run 并保证结果/失败收口。
- **处理方式**：**使用 LangChain/LangGraph 替换**。组合职责拆成 graph factory、FastAPI dependency 和 run service，不保留巨型 Runner 类。
- **复用代码或设计**：复用依赖注入、稳定 run/thread ID、所有终止路径都有最终状态、测试使用 fake provider 的设计。
- **框架能力**：compiled graph、RunnableConfig、checkpointer、FastAPI dependency injection。
- **放弃原因**：原 Runner 同时负责事件文件、session 文件、子 Agent、MCP 和 loop，第一版复制会成为新的上帝对象。
- **失去能力**：没有 events.jsonl、extra_handlers 和全局 bus 注入点。
- **面试追问**：组合根放在哪里？如何测试 graph factory？为什么 thread ID 不等于 HTTP request ID？

### 4. AgentLoop

- **原项目解决的问题**：`src/kama_claude/core/loop.py` 手写 plan/observe/act 循环、消息配对、步数终止、工具失败回填和 compact；`test_loop.py` 覆盖核心路径。
- **新项目仍有的问题**：需要模型/工具循环、条件路由、失败反馈和硬终止条件。
- **处理方式**：**使用 LangChain/LangGraph 替换**。
- **复用代码或设计**：复用工具错误返回模型、max_steps、CancelledError 传播、每步可观察的原则；将这些转成图边和测试。
- **框架能力**：StateGraph、ToolNode、conditional edges、Command、interrupt、checkpoint。
- **放弃原因**：手写 while 会与 LangGraph 的持久化、恢复和路由机制重复，并增加审批恢复难度。
- **失去能力**：不再有一个可从头读到尾的单函数 ReAct loop；需通过图和状态理解运行。
- **面试追问**：显式图比 while 好在哪里？循环上限存 State 还是配置？ToolNode 错误处理边界是什么？

### 5. ExecutionContext

- **原项目解决的问题**：`src/kama_claude/core/context.py` 保存 goal、messages、step、状态、notes/context 和 system prompt，并处理 Anthropic tool_result 配对。
- **新项目仍有的问题**：节点之间必须共享计划、证据、Patch、审批、测试和重试状态。
- **处理方式**：**使用 LangChain/LangGraph 替换**为显式 State + Pydantic 子模型。
- **复用代码或设计**：复用“状态与消息分离”“工具结果保持配对”“持久上下文分层”的设计。
- **框架能力**：MessagesState/add_messages reducer、checkpointer、typed state schema。
- **放弃原因**：原 dataclass 只适合单循环，无法表达审批、Patch 哈希和多节点 writer/readers。
- **失去能力**：不再直接兼容 Anthropic 原生 messages JSON；由 LangChain 标准消息负责转换。
- **面试追问**：哪些字段不能进 messages？reducers 何时需要？checkpoint 中为何不能存客户端对象？

### 6. ToolRegistry

- **原项目解决的问题**：`src/kama_claude/core/tools/base.py`、`src/kama_claude/core/tools/registry.py`、`src/kama_claude/core/tools/invocation.py` 管理 schema、查找、超时、错误分类、重试、权限和事件；相应测试覆盖 registry/invocation/retry。
- **新项目仍有的问题**：需要模型可见工具 schema、参数校验、调用结果和错误反馈。
- **处理方式**：**框架替换注册，RepoPilot 自定义安全执行**。LangChain 提供 BaseTool/schema/message；P3 `SafeToolExecutor` 负责顺序、策略和审计。
- **复用代码或设计**：复用参数先校验、结构化 ToolResult 和“可预期错误回填 Agent”的原则；P3 明确不迁移自动重试。
- **框架能力**：StructuredTool、`BaseTool.get_input_schema()`、Pydantic args schema、ToolMessage/status；没有采用预构建 ToolNode。
- **放弃原因**：自建 registry 和 Anthropic schema 转换与 LangChain 重复；统一 `runtime_error` 自动重试可能重复副作用。
- **失去能力**：不保留同名覆盖、所有工具统一自动重试和自定义 EventBus 生命周期事件。
- **面试追问**：哪些异常交给 ToolNode？为什么副作用工具不进入 ToolNode？工具 schema 如何影响模型行为？

### 7. 文件读取工具

- **原项目解决的问题**：`src/kama_claude/core/tools/builtin/read_file.py` 限制 512 KB、UTF-8 replacement、拒绝 `..`；`test_read_file.py` 验证截断和遍历。
- **新项目仍有的问题**：必须按需读取代码，同时阻止越出目标仓库和过大上下文。
- **处理方式**：**修改后迁移**。
- **复用代码或设计**：复用字节上限、截断标记、FileNotFound 分类和 focused read；增加行范围、hash 和统一 WorkspaceGuard。
- **框架能力**：LangChain tool schema；路径安全不交给框架。
- **放弃原因**：原实现只检查 `..`，却允许绝对路径；未处理 symlink、盘符、UNC、审批后竞态。
- **失去能力**：第一版拒绝工作区外的所有读取，即使用户认为合理；大文件只能分段读。
- **面试追问**：为什么 `..` 过滤不够？symlink 如何绕过？read 与 apply 如何共享同一 root of trust？

### 8. 文件写入工具

- **原项目解决的问题**：`src/kama_claude/core/tools/builtin/write_file.py` 写/覆盖文本、创建父目录、限制 1 MB，并由 PermissionManager 审批。
- **新项目仍有的问题**：需要修改文件，但必须让用户先看到精确 Patch。
- **处理方式**：**第一版删除通用写工具**；P4 用模型可见但不写入的 `propose_patch` 与节点专用 `PatchApplicator` 替代。
- **复用代码或设计**：只复用内容大小限制、UTF-8 和错误结果思路；P4 独立实现双哈希、完整 Diff、审批后复核和同目录原子替换，不实现自动回滚。
- **框架能力**：approval 由 LangGraph interrupt；写入逻辑不由 LangChain 替代。
- **放弃原因**：通用 write_file 允许模型在审批后重写任意内容，审批对象与实际副作用难绑定。
- **失去能力**：模型不能创建、删除或重命名文件；P4 只支持一个既有普通文本文件的完整内容替换，不支持二进制和批量 Patch。
- **面试追问**：为什么批准工具名不等于批准内容？如何防 TOCTOU？多文件写如何处理半失败？

### 9. Bash 工具

- **原项目解决的问题**：`src/kama_claude/core/tools/builtin/bash.py` 执行任意 shell、超时、合并输出；权限策略用正则判断危险命令。
- **新项目仍有的问题**：P5 只需要运行固定 pytest；Git Diff 仍是后续独立只读能力。
- **处理方式**：**第一版删除**通用 Bash；P5 已实现内部固定 `PytestRunner`，它不是模型工具或 API 命令入口。
- **复用代码或设计**：复用 subprocess 超时、kill/reap、输出上限和非零退出分类的测试思路。
- **框架能力**：无；使用 `create_subprocess_exec` 的确定性 Python 逻辑。
- **放弃原因**：命令字符串正则无法可靠建立安全边界，`shell=True` 存在注入和平台差异。
- **失去能力**：不能任意运行 formatter、grep、pip 或项目脚本。
- **面试追问**：参数数组为何比 shell 字符串安全？pytest 插件仍可能执行代码怎么办？超时后如何避免孤儿进程？

### 10. TaskManager

- **原项目解决的问题**：`src/kama_claude/core/task/model.py`、`src/kama_claude/core/task/manager.py` 和 task tools 用 JSON 文件保存任务、状态和 blocked_by；测试覆盖 CRUD/级联。
- **新项目仍有的问题**：需要结构化计划、当前步骤和重规划历史。
- **处理方式**：**使用 LangChain/LangGraph 替换**为 State 中的 `ExecutionPlan` 与 plan_version。
- **复用代码或设计**：复用小整数步骤、状态简化、依赖显式和“计划应可读可验收”的设计。
- **框架能力**：Pydantic structured output、State、checkpoint。
- **放弃原因**：每 run 一组 task JSON 与 checkpoint 重复；blocked_by 在原系统仅建议性，不控制真正路由。
- **失去能力**：没有可手工 `cat` 的 `.tasks/` 文件，也不提供 task_create/update 给模型。
- **面试追问**：计划是认知状态还是控制状态？重规划是替换还是追加？如何保证模型计划不直接控制危险边？

### 11. EventBus

- **原项目解决的问题**：`src/kama_claude/core/events/bus.py` 解耦 runner、writer、TUI 和 IPC broadcaster；测试验证顺序。
- **新项目仍有的问题**：需要观察节点进度，但没有多客户端/TUI 广播需求。
- **处理方式**：**第一版删除**。
- **复用代码或设计**：复用核心与展示解耦、事件需有稳定类型的思想。
- **框架能力**：LangGraph stream updates、callbacks、checkpoint state；FastAPI 查询状态。
- **放弃原因**：单进程、轮询 HTTP 下自建 bus 是重复抽象，还会引入背压/订阅生命周期问题。
- **失去能力**：没有全局 topic glob、多个实时订阅者和子 Agent event bridge。
- **面试追问**：何时值得引入 EventBus？stream 与 domain event 是否等价？慢订阅者如何造成背压？

### 12. EventWriter

- **原项目解决的问题**：`src/kama_claude/core/events/writer.py` 把每个事件 flush 到 events.jsonl，崩溃时保留过程证据。
- **新项目仍有的问题**：需要恢复状态和输出 Trace。
- **处理方式**：**第一版删除**自定义 JSONL writer。
- **复用代码或设计**：复用“首尾完整、失败也留证据、写入失败不应掩盖主错误”的测试原则。
- **框架能力**：SQLite checkpoint 保存 State；trace_events 保存小型节点时间线。
- **放弃原因**：事件日志和 checkpoint 双写会产生一致性问题；一天 Demo 不需要回放协议。
- **失去能力**：无法用 `tail -f events.jsonl` 观察逐 token/tool 事件。
- **面试追问**：checkpoint 与 event sourcing 有何区别？双写如何保证一致性？为什么 trace 不能替代业务状态？

### 13. TraceWriter

- **原项目解决的问题**：`src/kama_claude/core/trace/record.py`、`src/kama_claude/core/trace/writer.py`、`src/kama_claude/core/trace/provider.py` 记录 IPC/EventBus/LLM 三层时间线，并异步落盘；测试覆盖顺序和 payload 开关。
- **新项目仍有的问题**：需要知道每个节点、模型、工具、审批和测试发生了什么。
- **处理方式**：**后续阶段再实现**完整观测；第一版先有 checkpoint 内的 `trace_events`，P6 再接可选 LangSmith。
- **复用代码或设计**：复用 run/thread correlation、payload 可关闭、延迟/usage 和敏感数据边界。
- **框架能力**：LangGraph astream/callbacks、LangSmith tracing。
- **放弃原因**：IPC/event/LLM 三层方向已不成立；自建无限 JSONL 会重复记录私有源码。
- **失去能力**：没有离线 `kama trace` 式三层回放和逐 API payload 文件。
- **面试追问**：trace、log、metric、checkpoint 的区别？私有代码如何脱敏？为什么 LangSmith 必须可选？

### 14. Permission Policy

- **原项目解决的问题**：`src/kama_claude/core/permissions/policy.py` 以 ALLOW/DENY/ASK、deny/allow patterns 和 outside-cwd 启发式评估工具。
- **新项目仍有的问题**：必须区分只读与副作用，并确保审批对象和真实 Patch 一致。
- **处理方式**：P3 已先迁移为只读 `ToolSafetyPolicy`；P4 再增加 Patch 专用审批策略，不继续做通用 shell 正则策略。
- **复用代码或设计**：复用 unknown fail closed、参数先于策略和越界不可被配置绕过；P3 不移植 ASK/Future/always cache。
- **框架能力**：interrupt 承担暂停；策略和哈希校验由自有代码完成。
- **放弃原因**：命令正则无法证明 shell 安全；always_allow(tool name) 粒度过粗。
- **失去能力**：不支持按工具永久允许/拒绝和自定义 regex allowlist。
- **面试追问**：审批应该绑定什么数据？永久授权为何危险？静态策略与动态人工判断如何分层？

### 15. PermissionManager

- **原项目解决的问题**：`src/kama_claude/core/permissions/manager.py` 用 Future 等待 TUI 回应，管理 once/always、超时和断连取消；测试覆盖并发等待。
- **新项目仍有的问题**：需要跨 HTTP 请求暂停并恢复文件修改。
- **处理方式**：**修改后迁移**，等待机制由 LangGraph interrupt + checkpointer 替换，保留自定义批准复核服务。
- **复用代码或设计**：复用一请求一决定、超时/取消默认拒绝、审批前参数校验和断连不应自动批准。
- **框架能力**：`interrupt()`、`Command(resume=...)`、thread_id、SQLite checkpointer。
- **放弃原因**：内存 Future 无法跨进程重启恢复；工具名缓存不能绑定 Patch 内容。
- **失去能力**：不提供 always_allow/always_deny 和 TUI 内联卡片；第一版只有 approve/reject。
- **面试追问**：interrupt 恢复为何从节点开头执行？副作用应放在 interrupt 前还是后？如何防重复 resume？

### 16. Session Store

- **原项目解决的问题**：`src/kama_claude/core/session/model.py`、`src/kama_claude/core/session/store.py`、`src/kama_claude/core/session/manager.py` 保存 meta/thread/notes/runs，锁住单 session 并恢复完整消息；测试覆盖 orphan tool_use。
- **新项目仍有的问题**：审批跨请求、运行状态查询和进程重启恢复。
- **处理方式**：**使用 LangChain/LangGraph 替换**为 SQLite Checkpointer 和 thread_id。
- **复用代码或设计**：复用单 session 串行、持久化前消息结构合法、状态明确和恢复时校验。
- **框架能力**：AsyncSqliteSaver、graph get_state/history、thread persistence。
- **放弃原因**：自建 thread.jsonl/meta 与 graph checkpoint 重复且难保证原子一致。
- **失去能力**：没有人类可直接编辑的 thread.jsonl/notes.md 和 one_shot/chat 两种 session 目录。
- **面试追问**：checkpoint 是否等于数据库 session？thread_id 如何设计？SQLite 多进程限制是什么？

### 17. Notes 和 Context

- **原项目解决的问题**：`src/kama_claude/core/memory/loader.py`、notes.md 和 context.system_prompt 形成 global/project/session 三层记忆。
- **新项目仍有的问题**：需要项目规则、用户任务和失败证据，但第一版不是长期聊天助手。
- **处理方式**：**后续阶段再实现**；P6 只装配工作区内受信任项目规则与限长运行摘要，不提供 note_save。
- **复用代码或设计**：复用稳定背景不伪装成 user message、不同来源分层并标注信任级别。
- **框架能力**：RunnableConfig context、State 和 prompt builder。
- **放弃原因**：自动长期 notes 会扩大 prompt injection、隐私和过期事实问题。
- **失去能力**：跨 run 主动记忆、用户全局 context 和 agent 自写 notes。
- **面试追问**：长期记忆与 checkpoint 有何区别？项目文件是否可信？哪些上下文不应进入 system prompt？

### 18. Context Compact

- **原项目解决的问题**：`src/kama_claude/core/compact/budget.py` 截断大 tool_result；`src/kama_claude/core/compact/compactor.py` 用 LLM 生成 handoff summary，并支持自动/手动压缩。
- **新项目仍有的问题**：代码和 pytest 输出可能迅速撑大上下文。
- **处理方式**：**后续阶段再实现**；P3 先在工具边界限长，P6 再做消息预算和可选摘要。
- **复用代码或设计**：复用原始证据与模型输入分离、截断要标记、摘要失败保持原状态和结构化 handoff。
- **框架能力**：LangChain message trimming/summarization 组合；checkpointer 保留状态。
- **放弃原因**：一天 Demo 的短任务不需要自动 LLM compact；过早引入会增加有损错误和额外调用。
- **失去能力**：第一版不支持 `/compact`、context_pct 水位和长会话自动续航。
- **面试追问**：截断与摘要差别？如何保留 tool call 配对？为什么原始测试输出不应全塞 messages？

### 19. Skills

- **原项目解决的问题**：`src/kama_claude/core/skills/loader.py` 和内建 Markdown 用三级覆盖、prompt 模板及工具白名单固化工作流。
- **新项目仍有的问题**：未来可能需要 review/fix/test 等可复用工作流，但第一版只有单一修复闭环。
- **处理方式**：**后续阶段再实现**。
- **复用代码或设计**：复用项目本地优先、工具白名单必须由 registry 实施而非 Prompt 劝说。
- **框架能力**：预编译 graph/subgraph 或受校验的 prompt profile。
- **放弃原因**：当前没有两个以上稳定工作流，先建 loader 是过度设计；手写 YAML 解析也不值得迁移。
- **失去能力**：无 slash command、项目级覆盖和即插即用 prompt 工作流。
- **面试追问**：Skill 是 prompt 还是能力边界？何时应该升级成 subgraph？白名单拼错如何启动时发现？

### 20. Subagents

- **原项目解决的问题**：`src/kama_claude/core/subagent/tool.py`、registry 和 agent profiles 支持隔离上下文、前后台运行、嵌套上限和事件桥。
- **新项目仍有的问题**：reviewer 最好与 executor 认知隔离，但第一版可先用确定性审查。
- **处理方式**：**后续阶段再实现**，P7 只评估一个只读 reviewer subgraph，不做并行。
- **复用代码或设计**：复用冷启动上下文、明确输入契约、工具白名单、嵌套/预算上限。
- **框架能力**：LangGraph subgraph 的 per-invocation persistence；不迁移 BackgroundTaskRegistry。
- **放弃原因**：并行子 Agent 会增加 checkpoint namespace、权限、成本和结果合并复杂度，不适合一天 Demo。
- **失去能力**：没有后台 agent_result、并行 reviewer、父子事件桥和递归 spawn。
- **面试追问**：subgraph 与 tool 调用区别？上下文隔离的收益/损失？为何 reviewer 默认只读？

### 21. MCP Client

- **原项目解决的问题**：`src/kama_claude/core/mcp/client.py`、`src/kama_claude/core/mcp/server.py`、`src/kama_claude/core/mcp/tool.py` 自建 JSON-RPC over stdio/TCP，发现并包装外部工具；只有 `test_mcp_tool.py`，无专用集成测试。
- **新项目仍有的问题**：第一版没有外部系统需求；未来可能接入标准 MCP 工具。
- **处理方式**：**使用 LangChain/LangGraph 替换，但 P7 之后才评估**；当前 P7 已选择 Reviewer Subgraph，不同时实现 MCP。
- **复用代码或设计**：复用 server 前缀防冲突、外部工具仍需权限/错误边界和 server 不可用应降级。
- **框架能力**：`langchain-mcp-adapters` 和官方 MCP SDK。
- **放弃原因**：自建协议容易落后于 MCP 版本，原 TCP transport 也不是当前适配器的主要抽象；测试覆盖不足。
- **失去能力**：暂不连接 stdio/HTTP MCP server，不支持外部 resources/prompts/tools。
- **面试追问**：为什么不用自研 MCP？外部工具如何继承审批策略？MCP tool schema 能否直接信任？

### 22. daemon

- **原项目解决的问题**：`src/kama_claude/core/app.py` 的常驻 daemon 让 TUI 断开不杀任务、统一 session/权限/MCP 生命周期。
- **新项目仍有的问题**：HTTP 服务需要进程生命周期，但无需桌面多客户端常驻核心。
- **处理方式**：**第一版删除** daemon 架构，单 FastAPI/ASGI 进程承载 API 和 graph。
- **复用代码或设计**：复用优雅取消、启动时组合依赖和关闭时回收子进程。
- **框架能力**：FastAPI lifespan、ASGI server。
- **放弃原因**：再套一层 daemon 会与 Web server 重复，扩大部署和调试面。
- **失去能力**：前端断开后的独立长期任务保证和多个本地客户端共享核心。
- **面试追问**：长请求断开怎么办？何时需要 worker/queue？FastAPI lifespan 如何清理 checkpointer？

### 23. IPC

- **原项目解决的问题**：`src/kama_claude/core/bus/commands.py`、`src/kama_claude/core/bus/envelope.py`、`src/kama_claude/core/transport/socket_*`、`src/kama_claude/core/transport/ipc_broadcaster.py` 提供 JSON-RPC/NDJSON、事件订阅和回放。
- **新项目仍有的问题**：需要外部调用和审批恢复，但 HTTP 足够。
- **处理方式**：**第一版删除**自定义 IPC，改用 FastAPI REST + Pydantic/OpenAPI。
- **复用代码或设计**：复用类型化命令、结构化错误、稳定 correlation ID 和输入先校验。
- **框架能力**：FastAPI routing、HTTP status、OpenAPI。
- **放弃原因**：自定义 framing、pending Future 和 topic broadcaster 对单 Web API 没有收益。
- **失去能力**：没有同连接双向事件推送、topic glob、断线回放和多客户端 socket。
- **面试追问**：REST 如何表达 interrupt/resume？何时需要 SSE/WebSocket？HTTP 幂等性如何处理重复审批？

### 24. CLI

- **原项目解决的问题**：`src/kama_claude/cli/main.py` 及 commands 提供 run/chat/core/trace 入口，是 daemon 的客户端。
- **新项目仍有的问题**：开发者要触发和演示 API，但 curl/OpenAPI 已足够。
- **处理方式**：**第一版删除**正式 CLI；后续若 API 稳定，再做薄客户端。
- **复用代码或设计**：复用清晰子命令和 Ctrl+C 语义的思路，不复用 socket client。
- **框架能力**：FastAPI Swagger UI、HTTP 客户端。
- **放弃原因**：API 尚未稳定时同时维护 CLI 会扩大 P0-P5 范围。
- **失去能力**：没有一条 `repopilot run` 命令、终端实时 token 和 trace follow。
- **面试追问**：CLI 应该包含业务逻辑吗？API schema 如何生成客户端？何时 CLI 值得成为验收入口？

### 25. TUI

- **原项目解决的问题**：`src/kama_claude/tui/app.py` 用 Textual 展示 token、工具块、审批、上下文水位和 session 输入。
- **新项目仍有的问题**：用户要看到 Patch 并审批，但第一版 JSON/OpenAPI 足以证明闭环。
- **处理方式**：**第一版删除**。
- **复用代码或设计**：复用审批信息必须可读、长输出默认折叠、UI 不能阻塞后台执行的原则。
- **框架能力**：无；第一版使用 HTTP response，未来另行选择 Web 前端。
- **放弃原因**：Textual、socket event pump 和焦点管理与 RepoPilot 核心价值无关，不适合一天 Demo。
- **失去能力**：没有实时流、审批卡片、折叠工具输出、slash completion 和上下文水位 UI。
- **面试追问**：为什么先 API 后 UI？审批 UX 最少要展示什么？实时流如何避免泄露敏感代码？

## 4. 迁移后的能力账本

第一版保留的核心能力是：结构化规划、只读工具循环、Patch 生成、人工审批、受控应用、pytest 反馈、有限重规划、Git Diff 审查、持久状态和 Trace 摘要。

主动舍弃的能力是：daemon/IPC/TUI、多客户端事件流、通用 shell、自由 write_file、task JSON、长期 notes、自动 compact、Skills、并行 Subagents 和自研 MCP。舍弃这些能力不是否认其价值，而是为了让一天 Demo 的每个关键安全不变量都能由少量代码和测试证明。

## 5. P2 实际迁移记录

- P1 `ToolCallingLoop` 已由 LangGraph `StateGraph`、自定义节点和条件边替换；旧实现可在提交 `aa39eeb` 查看。
- KamaClaude AgentLoop 只提供“消息配对、失败回填、有限终止”问题定义，没有复制实现。
- KamaClaude TaskManager 在 P2 未迁移；本阶段没有 Planner、ExecutionPlan 或任务 CRUD。
- LangGraph 负责 State reducer、节点调度、条件路由和编译执行；LangChain 继续提供 Chat Model、BaseTool 和标准消息类型。
- RepoPilot 自定义 ToolNode 保留同轮顺序执行、稳定 JSON 错误与审计记录；评估后没有采用 `langgraph.prebuilt.ToolNode`。
- EventBus 不迁移；P2 未启用 Checkpointer、interrupt、streaming 或 Trace。

## 6. P3 实际迁移记录

- `tools/contracts.py` 独立定义 effect、phase、category、code、Envelope 和脱敏审计，不复制 KamaClaude dataclass。
- `SafeToolExecutor` 独立实现 Dispatch → Validation → Policy → Execution → Normalization；KamaClaude Invocation 只提供“参数先校验、失败回填”问题定义。
- `ToolSafetyPolicy` 用三个显式 read-only mapping；未分类、write、command 在执行前 fail closed。没有移植 PermissionManager、Future、缓存、EventBus 或重试。
- `WorkspaceGuard` 保留 P1/P2 的 ADS 修复并覆盖 UNC、设备路径、保留名、尾随点/空格、敏感密钥名、symlink/junction 和 resolve 后 containment。
- Graph 节点与边保持 P2 拓扑；P3 没有 Policy Node、Approval Node、interrupt、Checkpointer、Session 或 thread_id。
- 所有工具失败以固定 Envelope 和 ToolMessage status 回填模型；API 仅返回脱敏记录，不返回原始参数、文件内容或异常。

## 7. P4 实际迁移记录

- KamaClaude `PermissionManager` 的“一次调用对应一次人工决定”问题由 LangGraph `interrupt()`、`Command(resume=...)`、checkpointer 和服务端 UUID thread_id 替换；没有迁移 Future、always cache、TUI、EventBus 或策略文件。
- P3 Policy 扩展为 `allow / require_approval / deny`：三个只读工具 allow，只有 `propose_patch` write 进入审批，其他 write、command、unknown 都 fail closed。
- KamaClaude `write_file` 未迁移。RepoPilot 的模型只提交 path/new_content/rationale；`PatchProposalBuilder` 读取一个既有 UTF-8 普通文件并计算完整 Diff 与 SHA-256，Proposal 阶段零写入。
- `ApprovalNode` 是唯一 interrupt 调用者，`ApplyPatchNode` 是唯一写入者。恢复后重新验证 workspace、链接、original/proposed hash、Diff/行数绑定和资源上限，再用同目录临时文件与 `os.replace`。
- P4 使用 `InMemorySaver`，只保证同一进程内跨请求恢复。P6 才计划 SQLite；checkpoint 不是长期用户记忆。
- P4 当时不支持 edit、多 Patch 批次、新建/删除/重命名、Shell、Planner、pytest 或自动修复；P5 只在其后增加固定 pytest 与有限修复循环，不改变 P4 历史边界。

## 8. P5 实际迁移记录

- 定向阅读 KamaClaude `core/tools/invocation.py`、`core/task/manager.py`、`core/compact/budget.py` 和 `core/runner.py`，只采用错误分类、有限次数、输出上限与终态清晰的问题定义，没有复制 Bash、daemon、EventBus、Task CRUD 或自动工具重试代码。
- 通用 Bash 继续删除。`PytestRunner` 独立实现固定参数序列、绝对当前解释器、workspace cwd、环境 allowlist、timeout、持续管道读取、硬输出上限与 best-effort 脱敏；API/模型不能传命令、参数、cwd、env 或 executable。
- pytest 官方 exit code 0–6 由 Python 映射为 `TestOutcome`。只有 exit code 1 是代码修复反馈；2–6、未知返回码、timeout、output limit 和 launch error 均进入确定性 Review/Report。
- P4 Apply 成功 ToolMessage 的时点演进为 Apply → Tester → 唯一 ToolMessage；reject、stale 和 Apply failure 仍立即回填。每个新 Proposal 都重新 interrupt 和审批。
- LangGraph 条件边替代手写重试循环；`max_steps` 限模型轮次，`max_repair_attempts` 限实际 Apply+pytest 次数，两者由 Python 独立裁决。
- Reviewer 是普通 Python 组件，只核对审批、一次性 context、ToolMessage、Patch/Test ID、文件 hash、exit code 和预算证据；P7 才可能评估 Reviewer Subgraph。
- P5 没有新增依赖，仍使用 `InMemorySaver`；没有实现 Planner、Git Diff Reviewer、SQLite、Trace、Shell、Docker、MCP、Dify 或 Subagent。

## 9. P6 实际迁移记录

| 参考主题 | RepoPilot P6 决策 | 结果 |
| --- | --- | --- |
| Session Store | 用 LangGraph AsyncSqliteSaver 替换，不迁移 JSONL Session/Manager | framework replacement |
| TraceWriter | 独立实现小型 RuntimeStore + TraceRecorder，不迁移 EventBus | adapted problem, new implementation |
| Context Budget/Compactor | 保留预算、原子工具交换与失败不破坏 State 的问题；采用确定性瞬时裁剪 | adapted problem, new implementation |
| daemon/IPC | 不迁移；FastAPI lifespan 管理单进程本地资源 | removed |

参考源码只读且未修改，没有复制 Session、Trace、EventBus 或 compactor 实现。P7 的 Reviewer Subgraph、MCP、Skills 和多 Agent 仍未开始。
