# Code Reuse Log

## 1. 当前状态

本文件记录候选复用与已执行结论。P0 已实现 RepoPilot 自有的配置、模型工厂和 FastAPI Application Factory；P1 已独立实现 LangChain 消息驱动的最小 Tool Calling 循环和三个只读工具。没有从 KamaClaude 复制产品代码，所有实现均针对当前框架与测试重新编写。

允许状态：

- `planned-direct-copy`：计划在许可证、接口和测试核对后原样或近乎原样复制。
- `planned-adaptation`：只迁移局部算法或设计，必须针对 RepoPilot 重写边界。
- `planned-framework-replacement`：原职责由 LangChain/LangGraph/FastAPI 等承担。
- `planned-removal`：第一版明确不保留。
- `undecided`：信息不足或需用实验决定。
- `adapted`：只采用已记录的设计原则，目标代码已独立重写并通过测试。
- `framework-replaced`：原职责已由选定框架或标准抽象承担，没有复制原实现。

当前没有 `planned-direct-copy` 项。原因是参考实现与 Anthropic 原生消息、daemon/EventBus、相对 CWD 和宽工具权限深度耦合；直接复制会把已决定删除的架构或不充分的安全边界带入新项目。任何未来直接复制都必须先确认参考项目许可证允许公开使用，并在本表新增逐文件记录。

## 2. 候选记录

| ID | 参考源码 | 计划目标 | 状态 | 计划复用范围 | 必须改变/验证 |
| --- | --- | --- | --- | --- | --- |
| CR-001 | `src/kama_claude/core/config.py` | `src/repopilot/infrastructure/config.py` | `adapted` | 配置优先级和启动时校验思路 | 已删除 daemon/MCP/TUI 配置；使用 Pydantic Settings 独立重写并补边界测试 |
| CR-002 | `src/kama_claude/core/llm/base.py`、`src/kama_claude/core/llm/provider.py`、`src/kama_claude/core/llm/types.py` | `src/repopilot/infrastructure/model_factory.py` | `framework-replaced` | Provider 注入和统一模型边界 | 已使用 LangChain `BaseChatModel` 与 `ChatOpenAI`；未复制 Anthropic content block 解析 |
| CR-003 | `src/kama_claude/core/runner.py` | `agent/graph.py`、API run service | `planned-framework-replacement` | 组合根、稳定 run ID、最终状态收口 | 用 compiled graph + dependencies；避免上帝类 |
| CR-004 | `src/kama_claude/core/loop.py` | P1 `src/repopilot/agent/loop.py`；未来 StateGraph nodes/edges | `adapted` | P1 已采用 max_steps 与工具错误回填原则 | P1 使用 LangChain messages 独立重写；未迁移取消、EventBus 或原生 Provider 解析；未来图迁移仍未开始 |
| CR-005 | `src/kama_claude/core/context.py` | `schemas/state.py` | `planned-framework-replacement` | messages 与领域状态分离、分层上下文 | 定义 reducers；增加 Patch/审批/测试字段；标准 LangChain messages |
| CR-006 | `src/kama_claude/core/tools/base.py` | `src/repopilot/schemas/agent.py` | `adapted` | P1 已采用 `success/error_type/error_message` 的结构化结果思想 | 使用 Pydantic 独立定义；retryable/details 未在 P1 提前实现 |
| CR-007 | `src/kama_claude/core/tools/registry.py` | P1 `ToolCallingLoop` 名称映射；未来 executor | `framework-replaced` | P1 只保留稳定工具名和唯一性约束 | 使用 LangChain `BaseTool` 加运行内字典；没有复制动态 Registry 或插件机制 |
| CR-008 | `src/kama_claude/core/tools/invocation.py` | `src/repopilot/agent/loop.py` 工具错误边界 | `adapted` | P1 已实现参数校验错误、未知工具与异常回填 | 不发 EventBus、不做超时/重试/审批；后续阶段仍按各自范围实现 |
| CR-009 | `src/kama_claude/core/tools/builtin/read_file.py` | `src/repopilot/tools/readonly.py` | `adapted` | P1 已采用字节上限、UTF-8 与截断标记原则 | 统一到当前 WorkspaceGuard，拒绝绝对/父级/敏感路径并增加二进制测试；未复制源码 |
| CR-010 | `src/kama_claude/core/tools/builtin/list_dir.py` | `src/repopilot/tools/readonly.py` | `adapted` | P1 已实现有界递归、条目上限和确定性排序 | 使用当前相对 workspace 结果模型，跳过敏感目录和 symlink；未迁移通用 ignore 系统 |
| CR-011 | `src/kama_claude/core/tools/builtin/write_file.py` | `services/patch_applier.py` | `planned-removal` | 不复用通用工具 | 新实现只能应用已批准 Patch，带 preimage/hash/回滚 |
| CR-012 | `src/kama_claude/core/tools/builtin/bash.py` | `services/test_runner.py` | `planned-removal` | 不复用任意命令接口 | 仅借鉴 timeout/kill/output limit 测试；固定 `create_subprocess_exec` |
| CR-013 | `src/kama_claude/core/task/model.py`、`src/kama_claude/core/task/manager.py` | State `ExecutionPlan` | `planned-framework-replacement` | 小整数步骤、明确状态与验收项 | 不写 `.tasks` JSON；计划由 Pydantic + checkpoint 保存 |
| CR-014 | `src/kama_claude/core/events/bus.py` | graph stream/trace_events | `planned-removal` | 只保留解耦原则 | 不创建自定义 publish/subscribe 抽象 |
| CR-015 | `src/kama_claude/core/events/writer.py` | SQLite checkpoint | `planned-removal` | 只保留失败留证据原则 | 不写 events.jsonl；测试 checkpoint 与最终 trace |
| CR-016 | `src/kama_claude/core/trace/record.py`、`src/kama_claude/core/trace/writer.py`、`src/kama_claude/core/trace/provider.py` | `schemas/trace.py`、可选 tracing adapter | `planned-adaptation` | correlation、latency、payload 开关 | P6 才实现；默认脱敏；LangSmith 可选；不记录 IPC 方向 |
| CR-017 | `src/kama_claude/core/permissions/policy.py` | `services/approval_policy.py` | `planned-adaptation` | unknown fail-closed、越界不可被缓存绕过、参数摘要 | 从命令 regex 改为 Patch hash/workspace/preimage 绑定 |
| CR-018 | `src/kama_claude/core/permissions/manager.py` | approval node/API | `planned-framework-replacement` | 一请求一决定、取消默认拒绝 | Future 改为 interrupt/resume；删除 always allow/deny |
| CR-019 | `src/kama_claude/core/permissions/storage.py` | 无 | `planned-removal` | 无 | 第一版不持久化通用工具授权 |
| CR-020 | `src/kama_claude/core/session/model.py`、`src/kama_claude/core/session/store.py`、`src/kama_claude/core/session/manager.py` | SQLite Checkpointer | `planned-framework-replacement` | 单 session 串行、恢复前校验 | 不复制 meta/thread/notes 文件格式 |
| CR-021 | `src/kama_claude/core/memory/loader.py` | P6 context loader | `planned-adaptation` | 小型 Markdown context 文件按需读取 | 标注信任来源、限制大小、禁止工作区外默认读取 |
| CR-022 | `src/kama_claude/core/compact/budget.py` | P6 context budget | `planned-adaptation` | tool_result 限长且带 omitted 标记 | 适配 ToolMessage；保留头尾；先做工具输出上限 |
| CR-023 | `src/kama_claude/core/compact/compactor.py` | P6 optional summarizer | `undecided` | 六段 handoff summary 与失败不破坏原状态 | 先测短 Demo 是否需要；不得默认自动有损压缩 |
| CR-024 | `src/kama_claude/core/skills/loader.py`、`src/kama_claude/core/skills/builtin/*` | P7 workflow profiles | `undecided` | 项目本地覆盖、工具白名单理念 | 不迁移手写 frontmatter parser；至少两个稳定工作流后再引入 |
| CR-025 | `src/kama_claude/core/agents/loader.py`、`src/kama_claude/core/agents/builtin/*` | P7 reviewer profile | `planned-adaptation` | reviewer 只读角色、工具白名单 | 改为 Pydantic 配置或编译期 subgraph；启动时验证工具名 |
| CR-026 | `src/kama_claude/core/subagent/registry.py` | 无 | `planned-removal` | 无 | 第一版不做后台并行任务 |
| CR-027 | `src/kama_claude/core/subagent/tool.py` | P7 reviewer subgraph | `planned-framework-replacement` | 冷启动隔离、预算和只读边界 | 使用 LangGraph per-invocation subgraph；不提供 spawn_agent 工具 |
| CR-028 | `src/kama_claude/core/mcp/client.py`、`src/kama_claude/core/mcp/server.py`、`src/kama_claude/core/mcp/tool.py` | P7 之后的 MCP adapter 候选 | `planned-framework-replacement` | server 名称前缀、错误降级 | 当前 P7 只做 reviewer；未来使用 `langchain-mcp-adapters`，外部 schema/权限仍需校验 |
| CR-029 | `src/kama_claude/core/app.py` | FastAPI composition/lifespan | `planned-removal` | 只借鉴启动/关闭清理原则 | 不复制 daemon、signal/socket handler 架构 |
| CR-030 | `src/kama_claude/core/transport/*`、`src/kama_claude/core/bus/*` | FastAPI routes/schemas | `planned-framework-replacement` | 类型化命令、结构化错误、correlation | HTTP/OpenAPI 替代 JSON-RPC/NDJSON/topic subscribe |
| CR-031 | `src/kama_claude/cli/*` | 无正式 CLI | `planned-removal` | 无 | P0-P7 使用 OpenAPI/curl/pytest 驱动 |
| CR-032 | `src/kama_claude/tui/app.py` | 无 | `planned-removal` | 只保留审批信息可读、UI 不阻塞的 UX 原则 | 第一版不引入 Textual 或 Web UI |

## 3. P0 执行记录

| 记录 | 源与目标 | 实际处理 | 许可证/复制结论 | 验证 |
| --- | --- | --- | --- | --- |
| P0-CR-001 | 参考 `core/config.py`；目标 `src/repopilot/infrastructure/config.py` | 只采用“配置来源可测试、启动边界校验”的原则；字段、Pydantic 校验器、dotenv 入口和安全导出均独立实现 | 没有复制源码或逐行改写，因此本阶段无直接复制许可证结论 | `tests/unit/test_config.py` 覆盖默认值、优先级、隔离、脱敏与非法边界 |
| P0-CR-002 | 参考 `core/llm/*`；目标 `src/repopilot/infrastructure/model_factory.py` | 用 LangChain Core 抽象替换原生 Provider/消息层，只保留可注入原则；没有迁移 usage、消息解析或重试 | 没有复制源码；使用项目依赖各自发布包 | `tests/unit/test_model_factory.py` 覆盖 Fake、未知 Provider、缺失 Key、参数和零网络构造 |
| P0-CR-029 | 参考 `core/app.py` 的显式组合原则；目标 `src/repopilot/api/app.py` | 用 FastAPI Application Factory 独立实现单进程组合根；不保留 daemon、socket、signal 或 EventBus 生命周期 | 没有复制源码；`CR-029` 的 daemon 实现仍为 `planned-removal` | `tests/integration/test_health_api.py` 覆盖独立测试配置、Fake 注入、Schema、脱敏和零模型调用 |

P0 定向阅读了上表所列参考文件及相关配置/Provider/最小连通测试，但没有扫描或修改整个 `reference_materials/`。`langchain` 元包因传递引入 LangGraph 而未使用；最终依赖为 `langchain-core` 加最小 Provider 集成，保持 P0 不包含 LangGraph。

## 4. P1 执行记录

| 记录 | 源与目标 | 实际处理 | 许可证/复制结论 | 验证 |
| --- | --- | --- | --- | --- |
| P1-CR-004 | 参考 `core/loop.py`；目标 `src/repopilot/agent/loop.py` | 只采用“工具结果回填、最大步数终止”的设计问题；核心循环使用 LangChain `AIMessage.tool_calls`、`ToolMessage` 和 `BaseTool` 独立实现 | 没有复制循环源码、EventBus 或 Provider content block 解析 | `tests/unit/test_tool_calling_loop.py` 覆盖直接回答、单/多调用、ID、错误回填、模型异常和最大步数 |
| P1-CR-007 | 参考 `core/tools/registry.py`；目标 P1 运行内名称映射 | 没有复制 Registry；三个固定 LangChain Tool 由小型字典查找，并在运行开始拒绝重名 | 无源码复制；动态 Registry/插件仍无当前需求 | 同一循环测试验证三工具绑定、未知工具和重名防线 |
| P1-CR-008 | 参考 `core/tools/invocation.py`；目标 `src/repopilot/agent/loop.py::_execute_tool` | 迁移“参数错误是可回填纠错信息”的原则；Pydantic 错误、未知工具和内部异常转换为稳定 ToolMessage | 没有复制超时、重试、事件发布或权限代码 | 参数缺失、多余参数、工具异常和结构化失败测试 |
| P1-CR-009/010 | 参考 `builtin/read_file.py`、`list_dir.py`；目标 `src/repopilot/tools/readonly.py` | 按当前 workspace 模型重写 list/read，并新增普通文本 `search_code`；只保留 UTF-8、上限和稳定排序原则 | 未复制参考源码；路径边界和 Result Schema 为 RepoPilot 独立实现 | `tests/unit/test_readonly_tools.py` 覆盖敏感目录、逃逸、二进制、截断和搜索 |
| P1-CR-014/015 | 参考 EventBus/EventWriter；P1 无目标实现 | 明确不迁移；结果只存在本次内存运行和 API 响应 | 无复制 | 源码与依赖审查确认无自定义事件总线或持久 Trace |
| P1-Provider | 参考原生 Provider tool-call 解析；目标 LangChain 消息接口 | 由 LangChain 标准化 `AIMessage.tool_calls` 替代，不解析字符串或 Provider content blocks | framework replacement，无参考解析代码复制 | 脚本模型测试直接构造标准 AIMessage，并检查 ToolMessage 往返 |

P1 只定向阅读 AgentLoop、ExecutionContext、ToolRegistry、Invocation、ReadFile 和对应测试，没有修改 `reference_materials/`。当前代码没有开始 StateGraph、审批、写文件、命令执行、会话或持久 Trace；表中的未来目标仍保持计划状态。

## 5. 测试思想候选

测试文件本身也不直接复制；只迁移可验证行为，并针对新架构改写：

| 参考测试 | 迁移后的测试主题 | 状态 |
| --- | --- | --- |
| `tests/unit/test_loop.py` | P1 已改写工具失败反馈、Tool Call ID、多调用和 max steps；条件边/取消仍属后续 | `adapted` |
| `tests/unit/test_runner.py` | run 最终状态、依赖注入、thread ID 一致性 | `planned-adaptation` |
| `tests/unit/test_read_file.py` | P1 已改写截断、缺失、二进制、absolute/parent/敏感路径测试；平台级 junction/UNC 留待完整安全阶段 | `adapted` |
| `tests/unit/test_permission_policy.py` | fail-closed；改成 Patch hash/workspace/preimage 不变量 | `planned-adaptation` |
| `tests/unit/test_permission_manager.py` | interrupt/resume、重复/过期/拒绝批准 | `planned-framework-replacement` |
| `tests/unit/test_tool_retry.py` | 区分模型/只读工具/pytest 重试，禁止副作用自动重试 | `planned-adaptation` |
| `tests/unit/test_session_store.py` | checkpoint 恢复和 state projection | `planned-framework-replacement` |
| `tests/unit/test_compactor.py` | 摘要失败保持状态、输出预算 | `undecided` |
| `tests/unit/test_spawn_agent_tool.py` | P7 reviewer subgraph 隔离和只读能力 | `planned-framework-replacement` |
| `tests/unit/test_mcp_tool.py` | P7 adapter 命名、不可用降级、schema 拦截 | `planned-framework-replacement` |
| `tests/integration/test_run_e2e.py` | P1 已用脚本模型改写离线 API 闭环和运行隔离；未增加真实模型测试 | `adapted` |
| `tests/integration/test_s5_permission_flow.py` | 批准路径、拒绝路径、新 Patch 重新审批 | `planned-adaptation` |

## 6. 执行规则

每次真正复用代码时必须在同一个开发阶段更新本表：记录源文件、目标文件、具体函数/行范围、许可证结论、修改摘要和新增测试。只有完成这些记录后，状态才允许从 `planned-*` 改为 `adapted`/`copied` 等未来状态；当前阶段不得提前标记完成。

参考资料位于被 `.gitignore` 忽略的私有目录。不得把其源码、课程原文或来源介绍复制到公开 README；公开文档只能记录独立形成的架构决策和必要的路径映射。
