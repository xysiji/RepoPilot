# Code Reuse Log

## 1. 当前状态

本文件只记录候选复用，不代表代码已经复制。当前阶段没有修改 `src/repopilot/`，没有从 KamaClaude 复制任何产品代码，也没有安装依赖。

允许状态：

- `planned-direct-copy`：计划在许可证、接口和测试核对后原样或近乎原样复制。
- `planned-adaptation`：只迁移局部算法或设计，必须针对 RepoPilot 重写边界。
- `planned-framework-replacement`：原职责由 LangChain/LangGraph/FastAPI 等承担。
- `planned-removal`：第一版明确不保留。
- `undecided`：信息不足或需用实验决定。

当前没有 `planned-direct-copy` 项。原因是参考实现与 Anthropic 原生消息、daemon/EventBus、相对 CWD 和宽工具权限深度耦合；直接复制会把已决定删除的架构或不充分的安全边界带入新项目。任何未来直接复制都必须先确认参考项目许可证允许公开使用，并在本表新增逐文件记录。

## 2. 候选记录

| ID | 参考源码 | 计划目标 | 状态 | 计划复用范围 | 必须改变/验证 |
| --- | --- | --- | --- | --- | --- |
| CR-001 | `src/kama_claude/core/config.py` | `src/repopilot/infrastructure/config.py` | `planned-adaptation` | 配置优先级和启动时校验思路 | 删除 daemon/MCP/TUI 配置；敏感值不进 State；补边界测试 |
| CR-002 | `src/kama_claude/core/llm/base.py`、`src/kama_claude/core/llm/provider.py`、`src/kama_claude/core/llm/types.py` | `infrastructure/model_factory.py` | `planned-framework-replacement` | Provider 注入、usage/error 观测设计 | 使用 LangChain ChatModel；不复制 Anthropic content block 解析 |
| CR-003 | `src/kama_claude/core/runner.py` | `agent/graph.py`、API run service | `planned-framework-replacement` | 组合根、稳定 run ID、最终状态收口 | 用 compiled graph + dependencies；避免上帝类 |
| CR-004 | `src/kama_claude/core/loop.py` | StateGraph nodes/edges | `planned-framework-replacement` | max_steps、工具错误回填、取消传播 | 用条件边/ToolNode/interrupt；循环预算进 State |
| CR-005 | `src/kama_claude/core/context.py` | `schemas/state.py` | `planned-framework-replacement` | messages 与领域状态分离、分层上下文 | 定义 reducers；增加 Patch/审批/测试字段；标准 LangChain messages |
| CR-006 | `src/kama_claude/core/tools/base.py` | `schemas/tools.py` | `planned-adaptation` | `content/is_error/error_type` 的结构化结果思想 | 改为 Pydantic discriminated result；细分 retryable 与 details |
| CR-007 | `src/kama_claude/core/tools/registry.py` | `tools/` + executor ToolNode | `planned-framework-replacement` | 工具名/schema/调用契约 | 使用 LangChain tools；禁止同名静默覆盖 |
| CR-008 | `src/kama_claude/core/tools/invocation.py` | tool wrapper/node error boundary | `planned-adaptation` | 参数先校验、超时、错误分类 | 不统一重试副作用；不发 EventBus；批准由 graph 管理 |
| CR-009 | `src/kama_claude/core/tools/builtin/read_file.py` | `tools/read_file.py` | `planned-adaptation` | 字节上限、UTF-8、截断标记 | 统一 WorkspaceGuard、行范围、hash、绝对路径/symlink/UNC 防护 |
| CR-010 | `src/kama_claude/core/tools/builtin/list_dir.py` | `tools/list_files.py` | `planned-adaptation` | max_depth/max_entries、确定性排序 | 统一 WorkspaceGuard、ignore globs、文件类型与结果模型 |
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

## 3. 测试思想候选

测试文件本身也不直接复制；只迁移可验证行为，并针对新架构改写：

| 参考测试 | 迁移后的测试主题 | 状态 |
| --- | --- | --- |
| `tests/unit/test_loop.py` | 条件边、工具失败反馈、max steps、取消传播 | `planned-adaptation` |
| `tests/unit/test_runner.py` | run 最终状态、依赖注入、thread ID 一致性 | `planned-adaptation` |
| `tests/unit/test_read_file.py` | 截断、缺失文件；新增 absolute/symlink/junction/UNC/drive 测试 | `planned-adaptation` |
| `tests/unit/test_permission_policy.py` | fail-closed；改成 Patch hash/workspace/preimage 不变量 | `planned-adaptation` |
| `tests/unit/test_permission_manager.py` | interrupt/resume、重复/过期/拒绝批准 | `planned-framework-replacement` |
| `tests/unit/test_tool_retry.py` | 区分模型/只读工具/pytest 重试，禁止副作用自动重试 | `planned-adaptation` |
| `tests/unit/test_session_store.py` | checkpoint 恢复和 state projection | `planned-framework-replacement` |
| `tests/unit/test_compactor.py` | 摘要失败保持状态、输出预算 | `undecided` |
| `tests/unit/test_spawn_agent_tool.py` | P7 reviewer subgraph 隔离和只读能力 | `planned-framework-replacement` |
| `tests/unit/test_mcp_tool.py` | P7 adapter 命名、不可用降级、schema 拦截 | `planned-framework-replacement` |
| `tests/integration/test_run_e2e.py` | fake model 的完整闭环；真实模型测试单独标记 | `planned-adaptation` |
| `tests/integration/test_s5_permission_flow.py` | 批准路径、拒绝路径、新 Patch 重新审批 | `planned-adaptation` |

## 4. 执行规则

每次真正复用代码时必须在同一个开发阶段更新本表：记录源文件、目标文件、具体函数/行范围、许可证结论、修改摘要和新增测试。只有完成这些记录后，状态才允许从 `planned-*` 改为 `adapted`/`copied` 等未来状态；当前阶段不得提前标记完成。

参考资料位于被 `.gitignore` 忽略的私有目录。不得把其源码、课程原文或来源介绍复制到公开 README；公开文档只能记录独立形成的架构决策和必要的路径映射。
