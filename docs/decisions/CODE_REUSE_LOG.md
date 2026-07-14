# Code Reuse Log

## 1. 当前状态

本文件记录候选复用与已执行结论。P0 已实现基础设施，P1 手写循环已在 P2 被 StateGraph 替换，P3 加入安全执行管线，P4 加入单文件 Patch、interrupt 审批和原子应用，P5 加入固定 pytest、有限修复、确定性 Review 与 Final Report。没有从 KamaClaude 复制产品代码。

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
| CR-003 | `src/kama_claude/core/runner.py` | `agent/graph.py`、`services/agent_service.py` | `framework-replaced` | 组合依赖与最终状态收口 | P2 使用 compiled graph + service；没有 run ID、daemon 或上帝类 |
| CR-004 | `src/kama_claude/core/loop.py` | `agent/nodes.py`、`agent/routing.py`、`agent/graph.py` | `framework-replaced` | max_steps、工具错误回填和消息配对原则 | P2 用显式 StateGraph 独立重写；未迁移取消、EventBus、compact 或原生 Provider 解析 |
| CR-005 | `src/kama_claude/core/context.py` | `agent/state.py` | `framework-replaced` | messages 与控制状态分离、追加语义 | P4 增加 JSON checkpoint-safe pending proposal/decision；测试字段仍未实现 |
| CR-006 | `src/kama_claude/core/tools/base.py` | `src/repopilot/tools/contracts.py` | `adapted` | 结构化 ToolResult 思想 | P3 独立定义 success/data/error、三维失败分类和稳定 JSON；不复制 dataclass |
| CR-007 | `src/kama_claude/core/tools/registry.py` | `agent/nodes.py::ToolNode` 名称映射 | `framework-replaced` | 稳定工具名和唯一性约束 | 使用 LangChain `BaseTool` 加构造期字典；没有动态 Registry、插件或预构建 ToolNode |
| CR-008 | `src/kama_claude/core/tools/invocation.py` | `tools/executor.py` + `agent/nodes.py` | `adapted` | 参数先校验、失败回填、ID 配对 | P4 增加 proposal preparation/approval 分支；不发 EventBus、不重试、不泄漏异常 |
| CR-009 | `src/kama_claude/core/tools/builtin/read_file.py` | `tools/readonly.py` + `tools/policy.py` | `adapted` | 字节上限、UTF-8 与截断原则 | P3 统一 WorkspaceGuard，并补 ADS/device/link/密钥文件边界；未复制源码 |
| CR-010 | `src/kama_claude/core/tools/builtin/list_dir.py` | `src/repopilot/tools/readonly.py` | `adapted` | P1 已实现有界递归、条目上限和确定性排序 | 使用当前相对 workspace 结果模型，跳过敏感目录和 symlink；未迁移通用 ignore 系统 |
| CR-011 | `src/kama_claude/core/tools/builtin/write_file.py` | `patching/proposal.py`、`patching/applicator.py` | `adapted` | 只采用 UTF-8/大小限制问题定义 | P4 独立实现单文件 Diff、双哈希、复核与原子替换；无源码复制、创建、批量或回滚 |
| CR-012 | `src/kama_claude/core/tools/builtin/bash.py` | `testing/pytest_runner.py` | `planned-removal` | 不复用任意命令接口 | 仅借鉴 timeout/kill/output limit 测试；固定 `create_subprocess_exec` |
| CR-013 | `src/kama_claude/core/task/model.py`、`src/kama_claude/core/task/manager.py` | State `ExecutionPlan` | `planned-framework-replacement` | 小整数步骤、明确状态与验收项 | 不写 `.tasks` JSON；计划由 Pydantic + checkpoint 保存 |
| CR-014 | `src/kama_claude/core/events/bus.py` | graph stream/trace_events | `planned-removal` | 只保留解耦原则 | 不创建自定义 publish/subscribe 抽象 |
| CR-015 | `src/kama_claude/core/events/writer.py` | SQLite checkpoint | `planned-removal` | 只保留失败留证据原则 | 不写 events.jsonl；测试 checkpoint 与最终 trace |
| CR-016 | `src/kama_claude/core/trace/record.py`、`src/kama_claude/core/trace/writer.py`、`src/kama_claude/core/trace/provider.py` | `schemas/trace.py`、可选 tracing adapter | `planned-adaptation` | correlation、latency、payload 开关 | P6 才实现；默认脱敏；LangSmith 可选；不记录 IPC 方向 |
| CR-017 | `src/kama_claude/core/permissions/policy.py` | `tools/policy.py`；P4 approval policy | `adapted` | unknown fail-closed、越界不可绕过 | P3 只实现 read-only effect/workspace 决策；不复制命令 regex、ASK、缓存或持久授权 |
| CR-018 | `src/kama_claude/core/permissions/manager.py` | approval node/API | `framework-replaced` | 一请求一决定、拒绝零写入 | P4 用 interrupt/resume + InMemorySaver；删除 Future、timeout、always 和 TUI |
| CR-019 | `src/kama_claude/core/permissions/storage.py` | 无 | `planned-removal` | 无 | 第一版不持久化通用工具授权 |
| CR-020 | `src/kama_claude/core/session/model.py`、`src/kama_claude/core/session/store.py`、`src/kama_claude/core/session/manager.py` | LangGraph Checkpointer | `planned-framework-replacement` | 恢复前校验 | P4 仅 InMemorySaver run cursor；P6 SQLite，不复制 meta/thread/notes |
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

P1 只定向阅读 AgentLoop、ExecutionContext、ToolRegistry、Invocation、ReadFile 和对应测试，没有修改 `reference_materials/`。本节是历史执行记录；StateGraph 已在下述 P2 记录中完成，审批、写文件、命令执行、会话和持久 Trace 仍未开始。

## 5. P2 执行记录

| 记录 | 源与目标 | 实际处理 | 许可证/复制结论 | 验证 |
| --- | --- | --- | --- | --- |
| P2-CR-003/004 | 参考 `core/runner.py`、`core/loop.py`；目标 `agent/graph.py`、`nodes.py`、`routing.py`、`services/agent_service.py` | P1 手写循环由 LangGraph StateGraph 替换；仅保留终止与消息协议问题，节点和边均独立实现 | 没有复制源码、EventBus、daemon 或 Provider 解析 | `test_agent_graph.py` 覆盖拓扑、完整协议、错误、预算和图复用 |
| P2-CR-005 | 参考 `core/context.py`；目标 `agent/state.py` | 用 TypedDict、`add_messages` 与追加 reducer 显式表达最小运行状态 | 没有复制 dataclass 或 Anthropic message 转换 | `test_agent_state.py` 覆盖初始化、reducer 和容器隔离 |
| P2-CR-007/008 | 参考 Registry/Invocation 的问题边界；目标自定义 `ToolNode` | 保留顺序执行、ID 配对和稳定错误审计；评估后不采用预构建 ToolNode | 无源码复制；LangChain 只提供 BaseTool/ToolMessage | `test_agent_nodes.py` 覆盖多调用、未知工具、参数错误、异常和继续执行 |
| P2-TaskManager | 参考 `core/task/model.py`、`manager.py` | 只阅读状态/终止思想，本阶段明确不迁移 | 无源码复制 | 源码扫描确认无 Planner、TaskManager 或 ExecutionPlan |
| P2-EventBus/Checkpointer | 参考 EventBus 与持久化职责 | 本阶段不迁移、不启用 | 无源码复制 | compiled graph 的 `checkpointer is None`，调用不使用 thread_id |

P2 的生产执行引擎只有一套。P1 `src/repopilot/agent/loop.py` 与对应循环测试已删除，历史实现保存在提交 `aa39eeb`。参考资料保持只读且未修改。

## 6. P3 执行记录

| 记录 | 源与目标 | 实际处理 | 许可证/复制结论 | 验证 |
| --- | --- | --- | --- | --- |
| P3-CR-006/008 | 参考 ToolResult/Invocation；目标 `contracts.py`、`executor.py` | 只采用校验先行和失败回填原则，独立设计 Envelope 与严格五阶段执行器 | 无源码复制；未迁移重试、EventBus、timeout 或审批 | `test_tool_contracts.py`、`test_safe_tool_executor.py` 用 Spy 证明顺序和脱敏 |
| P3-CR-009 | 参考 ReadFile；目标 `readonly.py` | 保留 UTF-8/截断问题，实际读取逻辑按现有 LangChain 工具重写 | 无源码复制 | `test_readonly_tools.py` 覆盖内容、排序、截断、二进制、编码和三工具边界 |
| P3-CR-017 | 参考 Permission Policy；目标 `policy.py` | 采用静态决策/fail-closed 原则；改成 effect mapping 和 canonical workspace policy | 无源码复制；未迁移 ASK/Future/always/regex command | `test_tool_policy.py` 覆盖 effect、ADS、UNC、设备名、symlink/junction |
| P3-Graph | 无直接参考代码；目标自定义 ToolNode | ToolNode 只遍历调用并委托 SafeToolExecutor；Graph 拓扑不变 | LangGraph framework integration | Graph/API 测试覆盖拒绝恢复、预算终止、顺序、ID 和脱敏 |

P3 没有新增依赖，也没有写文件、Patch、命令、subprocess、审批、interrupt、Checkpointer、Session、自动重试或 P4 节点。`reference_materials/` 保持只读。

## 7. P4 执行记录

| 记录 | 源与目标 | 实际处理 | 许可证/复制结论 | 验证 |
| --- | --- | --- | --- | --- |
| P4-CR-011 | 参考 `builtin/write_file.py`；目标 `patching/proposal.py`、`applicator.py` | 只保留 UTF-8 与限长问题；独立实现单既有文件、系统 Diff、双哈希、同目录原子替换 | 无源码复制；未迁移创建目录/文件或通用覆盖工具 | proposal/applicator 单测覆盖零写入、stale、链接、资源、replace/验证 |
| P4-CR-017 | 参考 Permission Policy；目标 `tools/policy.py` | 将 P3 二态兼容字段升级为 allow/require_approval/deny，只有注册的 propose_patch 可审批 | 无源码复制；未迁移 bash regex/永久授权 | Policy/Executor Spy 证明非法参数和拒绝均不执行 |
| P4-CR-018 | 参考 PermissionManager；目标 Approval Node、AgentService、API | LangGraph dynamic interrupt + Command resume 替换 Future/TUI/EventBus；run 级锁串行化同进程重复决定 | framework replacement；未复制 manager | Graph/API 测试覆盖 approve/reject/mismatch/顺序与并发 repeat/same thread |
| P4-CR-020 | 参考 Session 恢复问题；目标 InMemorySaver | 只用 run_id/thread_id 恢复 pending graph，不实现用户会话或重启持久化 | framework replacement；无存储格式复制 | 新 saver 无法恢复旧 run 的限制测试 |

P4 未新增第三方依赖，未修改参考资料，也未实现 Shell、Planner、pytest、SQLite、Trace 或自动修复。

## 8. P5 执行记录

| 记录 | 源与目标 | 实际处理 | 许可证/复制结论 | 验证 |
| --- | --- | --- | --- | --- |
| P5-CR-008 | 参考 `core/tools/invocation.py`；目标 `testing/feedback.py`、Tester Node | 采用“结构化失败进入下一轮”的问题定义；独立实现 Patch+Test 事实 Envelope、exit 1 可恢复分类和原 ID 回填 | 无源码复制；未迁移工具自动 retry、EventBus 或异常文本 | feedback/Tester/repair-loop 测试覆盖唯一 ToolMessage、ID、预算和放弃 |
| P5-CR-003 | 参考 `core/runner.py`；目标 `testing/pytest_runner.py` | 只采用 runner 负责终态的问题边界；独立实现唯一固定 pytest 子进程入口 | 无源码复制；明确删除任意 Bash、daemon 和事件转发 | real runner 测试覆盖 pass/fail/no-tests/import/collection/timeout/output |
| P5-CR-022 | 参考 `core/compact/budget.py`；目标 Runner/feedback 输出治理 | 采用“输出必须有硬上限和明确截断”原则；实现共享字节预算、UTF-8 replacement、ANSI/control 清理和路径/secret best-effort redaction | 无源码复制；未迁移上下文压缩器 | unit/integration 测试覆盖无限输出停止、字符上限和敏感值 |
| P5-Task | 参考 TaskManager 与 tool-retry 测试；目标 State/routing | 只采用显式状态和有限次数；`model_calls` 与 `repair_attempts` 分离，由条件边裁决 | 无源码复制；没有 Task CRUD、Planner 或自动审批 | 两轮真实 pytest、一次耗尽、模型放弃与 infra 不重试测试 |
| P5-Review | 无对应参考实现；目标 `review/` | RepoPilot 独立实现确定性证据审查和安全 Final Report | 新实现；不调用 LLM，不建 Subgraph | hash、Patch/Test ID、重复 ToolMessage、终态报告矩阵测试 |

P5 未新增第三方依赖，未修改参考资料，也未实现通用命令、Planner、Git Diff reviewer、自动回滚、SQLite、Trace、LLM Reviewer 或 P6/P7 能力。

## 9. 测试思想候选

测试文件本身也不直接复制；只迁移可验证行为，并针对新架构改写：

| 参考测试 | 迁移后的测试主题 | 状态 |
| --- | --- | --- |
| `tests/unit/test_loop.py` | P2 已把失败反馈、Tool Call ID、多调用和 max steps 迁入节点/Graph 测试；取消仍属后续 | `adapted` |
| `tests/unit/test_runner.py` | run 最终状态、依赖注入、thread ID 一致性 | `planned-adaptation` |
| `tests/unit/test_read_file.py` | P1 已改写截断、缺失、二进制、absolute/parent/敏感路径测试；平台级 junction/UNC 留待完整安全阶段 | `adapted` |
| `tests/unit/test_permission_policy.py` | fail-closed 与 Patch workspace/preimage 不变量 | `adapted` |
| `tests/unit/test_permission_manager.py` | interrupt/resume、重复/过期/拒绝批准 | `framework-replaced` |
| `tests/unit/test_tool_retry.py` | P5 已区分 exit 1 修复反馈与基础设施终态；禁止相同 Patch/pytest 自动重试 | `adapted` |
| `tests/unit/test_session_store.py` | checkpoint 恢复和 state projection | `planned-framework-replacement` |
| `tests/unit/test_compactor.py` | 摘要失败保持状态、输出预算 | `undecided` |
| `tests/unit/test_spawn_agent_tool.py` | P7 reviewer subgraph 隔离和只读能力 | `planned-framework-replacement` |
| `tests/unit/test_mcp_tool.py` | P7 adapter 命名、不可用降级、schema 拦截 | `planned-framework-replacement` |
| `tests/integration/test_run_e2e.py` | P1 已用脚本模型改写离线 API 闭环和运行隔离；未增加真实模型测试 | `adapted` |
| `tests/integration/test_s5_permission_flow.py` | 批准路径、拒绝路径、新 Patch 重新审批 | `adapted` |

## 10. 执行规则

每次真正复用代码时必须在同一个开发阶段更新本表：记录源文件、目标文件、具体函数/行范围、许可证结论、修改摘要和新增测试。只有完成这些记录后，状态才允许从 `planned-*` 改为 `adapted`/`copied` 等未来状态；当前阶段不得提前标记完成。

参考资料位于被 `.gitignore` 忽略的私有目录。不得把其源码、课程原文或来源介绍复制到公开 README；公开文档只能记录独立形成的架构决策和必要的路径映射。
