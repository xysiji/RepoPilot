# P2 面试题答案：LangGraph StateGraph

## 一、概念题

### 1. State、Node、Edge、START 和 END

- **标准答案**：State 是一次图调用的共享事实；Node 读取 State 并返回局部更新；Edge 决定下一个节点；START 是虚拟入口；END 是终止标记。P2 只有 model、tools 两个业务节点。
- **真实源码**：`src/repopilot/agent/state.py::AgentState`；`src/repopilot/agent/graph.py::build_agent_graph`。
- **常见错误回答**：把 State 说成全局会话缓存，或认为 Edge 会自动让 LLM 选择。
- **面试表达**：图把数据、动作和控制流拆开；节点做事，边路由，State 保存可验证事实。
- **追问 1 答案**：局部 update 配合 reducer 可避免重复复制历史，并明确每个节点写哪些字段。
- **追问 2 答案**：`START -> model`；model 根据 status/tool_calls 去 tools 或 END；tools 根据预算后 status 回 model 或 END。

### 2. reducer 与 add_messages

- **标准答案**：reducer 定义同一 State 字段旧值与新 update 的合并方式。`add_messages` 对新消息追加，并按消息 ID 支持更新；不是简单用新列表覆盖旧列表。
- **真实源码**：`src/repopilot/agent/state.py::AgentState`；`tests/unit/test_agent_state.py::test_message_and_execution_reducers_append_partial_node_updates`。
- **常见错误回答**：声称所有 list 都会由 LangGraph 自动 append。
- **面试表达**：是否追加不是 list 类型决定的，而是 State annotation 中的 reducer 决定的。
- **追问 1 答案**：审计记录只需顺序追加，所以用 `operator.add`；status、计数、final answer 是明确单 writer 的当前值，应替换。
- **追问 2 答案**：状态 reducer 单测会先发现 HumanMessage 被新 AIMessage 覆盖；完整 Graph 的下一轮消息协议测试也会失败。

### 3. model_calls、max_steps 与 recursion limit

- **标准答案**：`model_calls` 是已尝试的模型节点次数；`max_steps` 是请求的业务模型轮次预算；recursion limit 是 LangGraph super-step 的防御上限，不表达产品终态。
- **真实源码**：`src/repopilot/agent/nodes.py::ModelNode/ToolNode`；`src/repopilot/services/agent_service.py::AgentService.run`。
- **常见错误回答**：把工具数量当 steps，或用 `GraphRecursionError` 返回正常超限。
- **面试表达**：业务预算必须产生可预测的 `max_steps_exceeded`，框架递归限制只兜底结构失控。
- **追问 1 答案**：model_calls 加 1；tool_executions 加 3；图 super-step 会经过一个 model 和一个 tools 节点。
- **追问 2 答案**：递归错误受图拓扑和框架计数影响，错误语义不稳定，也可能留下未配对 ToolMessage；正常终止应由 State 和路由完成。

### 4. Tool Calling 消息协议

- **标准答案**：带 tool calls 的 AIMessage 必须先进入历史；每个调用都要有相同 ID 的 ToolMessage，下一轮模型才能把结果对应到请求。P2 不解析字符串猜工具调用。
- **真实源码**：`src/repopilot/agent/nodes.py::ModelNode` 与 `ToolNode`；`tests/unit/test_agent_graph.py::test_tool_result_is_fed_to_next_model_round_with_matching_call_id`。
- **常见错误回答**：只把工具文本拼进下一条 HumanMessage，或只回填成功工具。
- **面试表达**：ToolMessage 不是日志，而是模型协议的一部分；顺序和 call ID 都是正确性约束。
- **追问 1 答案**：严格按 `AIMessage.tool_calls` 原顺序串行执行并按同顺序追加 ToolMessage。
- **追问 2 答案**：失败也是一次 observation；漏回填会破坏协议，且让模型无法纠正参数或选择替代工具。

### 5. compiled graph、Checkpointer 与 invoke State

- **标准答案**：compiled graph 保存可执行结构和节点依赖；每次 invoke 的 State 是调用输入和运行结果；Checkpointer 才负责按 thread 持久化中间状态。P2 compile 不传 Checkpointer。
- **真实源码**：`src/repopilot/agent/graph.py::build_agent_graph`；`tests/unit/test_agent_graph.py::test_compiled_graph_reuse_does_not_share_state_between_invocations`。
- **常见错误回答**：认为 compiled graph 本身会自动记住全部请求。
- **面试表达**：复用执行计划不等于复用执行数据；P2 每次都传全新初始 State。
- **追问 1 答案**：节点不保存消息，`create_initial_state` 每次新建列表，且无 checkpointer/store 将旧 State 注入新调用。
- **追问 2 答案**：要定义稳定 thread_id、存储生命周期、并发规则、恢复时外部 workspace 重验、敏感字段治理和 resume 契约。

## 二、源码题

### 6. Graph Builder

- **标准答案**：builder 先复制工具列表并拒绝重名，再 `model.bind_tools()`；注册自定义 model/tools 节点；添加 START 固定边和两组带 path map 的条件边；最后无持久化 `compile()`。
- **真实源码**：`src/repopilot/agent/graph.py::build_agent_graph`。
- **常见错误回答**：说服务层循环调用节点，或声称使用了预构建 Agent。
- **面试表达**：图结构只在 builder 定义，service 只准备 State、ainvoke 和投影结果。
- **追问 1 答案**：`build_agent_graph` 用工具名集合与工具列表长度比较，重名抛 ValueError。
- **追问 2 答案**：`tests/unit/test_agent_graph.py::test_graph_topology_is_explicit_and_compiled_without_checkpointer`。

### 7. ModelNode

- **标准答案**：节点 await 已绑定模型的 `ainvoke(messages)`；调用计数加一；非 AIMessage、空最终内容和异常分别形成稳定终态；有 tool calls 时只追加 AIMessage 并保持 running；无调用且内容非空时 success。
- **真实源码**：`src/repopilot/agent/nodes.py::ModelNode.__call__`；对应 `test_agent_nodes.py`。
- **常见错误回答**：在 ModelNode 内直接执行 tool_calls，或把带工具调用的 content 当最终答案。
- **面试表达**：ModelNode 只生产决策消息，不消费工具调用；动作边界在 ToolNode。
- **追问 1 答案**：增加，因为一次真实模型调用已发生并消耗预算，即使该调用抛错。
- **追问 2 答案**：节点没有工具映射或 `tool.invoke()`；测试传入不存在的工具名仍只得到 running update，不产生 ToolMessage/执行记录。

### 8. ToolNode

- **标准答案**：节点读取最后 AIMessage，按原列表串行查找工具、调用并构造 ToolMessage/ToolExecutionRecord。未知、校验、异常分别映射稳定 JSON；失败不 break。
- **真实源码**：`src/repopilot/agent/nodes.py::ToolNode`；`tests/unit/test_agent_nodes.py`。
- **常见错误回答**：并行调用同轮工具，或把 Pydantic 堆栈原样回给模型。
- **面试表达**：自定义 ToolNode 的价值是保留项目级顺序、错误分类、ID 和审计语义。
- **追问 1 答案**：`invalid_tool_arguments` 与 `tool_execution_error`；未知工具是 `unknown_tool`。
- **追问 2 答案**：先补齐本 AIMessage 的全部 ToolMessage，保证协议完整；然后设 `max_steps_exceeded`，不再回模型。

### 9. 路由函数

- **标准答案**：模型后：status 非 running 则 END；running 且最后 AIMessage 有 calls 则 tools；否则 END。工具后：running 则 model，否则 END。它们不写 State、不调用依赖。
- **真实源码**：`src/repopilot/agent/routing.py::route_after_model/route_after_tools`；`tests/unit/test_agent_routing.py`。
- **常见错误回答**：在 router 内增加 model_calls 或执行工具。
- **面试表达**：节点产生事实，路由只读取事实决定目标，因此分支可单测且没有隐藏副作用。
- **追问 1 答案**：防御性 END；合法的直接答案已由 ModelNode 设置 success，非法 running 状态也不会失控循环。
- **追问 2 答案**：path map 把返回值限定到已知目标，图渲染不会出现可能通往所有节点的模糊边。

### 10. API 调用链

- **标准答案**：FastAPI 校验 `AgentRunRequest`，依赖提供 Settings 和可选模型；无 override 时模型工厂构造；AgentService 构建只读工具和 compiled graph；run 创建初始 State，`ainvoke()`，再投影为 `AgentRunResult`。
- **真实源码**：`src/repopilot/api/routes/agent.py::run_agent`；`services/agent_service.py::AgentService`。
- **常见错误回答**：说 API 直接调用模型或直接序列化 LangGraph State。
- **面试表达**：HTTP 层只做边界与错误码，service 做图组合，nodes 做运行职责。
- **追问 1 答案**：`run_agent` 调用模型工厂时捕获 `ModelFactoryError`，稳定返回 503 `model_not_configured`。
- **追问 2 答案**：原始 State 含完整 messages 和工具全文，既破坏 P1 契约也扩大敏感信息暴露面；API 只返回受 Schema 约束的摘要。

## 三、设计取舍题

### 11. Graph 与普通循环

- **标准答案**：P1 循环适合学习最小协议；P2 显式化状态写入、分支和终止，为后续审批/反馈/恢复提供可组合边界，并能独立测试 nodes/routes/topology。
- **真实源码**：P1 历史提交 `aa39eeb` 的 `agent/loop.py`；当前 `agent/graph.py`。
- **常见错误回答**：泛称 Graph “性能更高”或“自动更安全”。
- **面试表达**：迁移收益是可观察和可验证的控制流，不是减少代码或自动获得安全。
- **追问 1 答案**：循环更短、调试栈直观、无框架学习成本，简单流程可能更合适。
- **追问 2 答案**：增加 LangGraph 依赖、State/reducer 设计、图级测试和节点间契约成本。

### 12. 自定义 ToolNode 与预构建 ToolNode

- **标准答案**：P2 需要严格串行处理同轮调用、稳定项目错误码、每个调用审计、批次完成后再判预算；小型自定义节点能直接表达并测试这些语义。
- **真实源码**：`src/repopilot/agent/nodes.py::ToolNode`；`docs/decisions/CODE_REUSE_LOG.md` P2 记录。
- **常见错误回答**：说预构建 ToolNode 不支持工具或不安全；结论应基于本项目适配成本，而非贬低框架。
- **面试表达**：框架节点可用，但当前项目的顺序与审计契约需要较多包装，因此选择可读的小型自定义边界。
- **追问 1 答案**：顺序执行、call ID 配对、稳定错误 JSON、ToolExecutionRecord 与完整批次终止。
- **追问 2 答案**：若后续语义与官方节点高度一致、包装显著减少且回归测试证明行为等价，可重新评估。

### 13. 不用 create_agent/AgentExecutor/Functional API

- **标准答案**：P2 目标就是显式学习和控制 State、Node、Edge、reducers 与终止条件；预构建 Agent 会隐藏这些边界，Functional API 更接近过程式编排。
- **真实源码**：`src/repopilot/agent/graph.py` 直接使用 `StateGraph`；源码扫描无这些 API。
- **常见错误回答**：认为这些 API 已废弃或绝对不能使用。
- **面试表达**：不是工具优劣，而是本阶段需要把控制流变成可见的学习与测试对象。
- **追问 1 答案**：每个节点/路由可单测，拓扑可检查，writer 与 reducer 清晰，max_steps 路径可精确断言。
- **追问 2 答案**：短小、线性、局部流程，或希望用普通控制结构快速封装为 Runnable 时可能更合适。

### 14. 不使用 Checkpointer/interrupt/thread_id

- **标准答案**：P2 只解决单请求图迁移。持久化和暂停恢复会带来存储、thread 身份、幂等、外部状态一致性和敏感数据治理，计划在后续阶段单独验证。
- **真实源码**：`build_agent_graph(...).compile()` 不传 checkpointer；`test_graph_topology...` 断言 None。
- **常见错误回答**：声称 LangGraph 不支持持久化，或无 Checkpointer 就不能循环。
- **面试表达**：先证明内存图语义，再把恢复作为独立风险面引入。
- **追问 1 答案**：当前不能跨进程恢复、暂停审批或查询历史 thread；请求结束后 State 不持久。
- **追问 2 答案**：即使内存 saver 也需要 thread_id、共享生命周期和隔离测试，会让 P2 同时承担恢复设计。

### 15. 运行依赖不进入 State

- **标准答案**：模型、工具、Settings、guard 是不可序列化或含敏感/运行连接的依赖，不是运行事实。Builder 把模型和工具注入节点，工具内部闭包持有 guard，服务持有 compiled graph。
- **真实源码**：`agent/state.py::AgentState` 字段集合；`agent/graph.py`；`tools/readonly.py::build_readonly_tools`。
- **常见错误回答**：为了“方便共享”把整个依赖容器塞进 State。
- **面试表达**：State 应是最小、可检查的数据面；依赖通过组合边界进入执行面。
- **追问 1 答案**：`build_agent_graph` 绑定模型/工具并构造节点；WorkspaceGuard 被只读工具闭包使用；Settings 只在 API/服务组合层读取。
- **追问 2 答案**：可能进入 checkpoint、trace、调试输出或 API 投影，增加密钥泄露和序列化失败风险。

## 四、异常与排错题

### 16. ToolMessage 不可见或 ID 不匹配

- **标准答案**：先检查最终/下一轮 messages 的实际类型与顺序，再检查 ToolNode 是否为每个 call append 相同 ID，最后检查 messages reducer 是否追加和路由是否回到 model。
- **真实源码**：`nodes.py::ToolNode`、`state.py::AgentState.messages`、`test_agent_graph.py::test_tool_result_is_fed_to_next_model_round_with_matching_call_id`。
- **常见错误回答**：直接改 Prompt，或只观察 API 摘要猜内部协议。
- **面试表达**：用脚本模型记录第二次收到的 messages，把协议问题定位为“生成、合并、路由”三段。
- **追问 1 答案**：先看 reducer 和 ToolNode，因为两者决定消息是否存在；路由只决定是否发生下一轮。
- **追问 2 答案**：脚本第一条返回一个带固定 ID 的 read_file call，第二条返回 final；断言第二次输入为 Human、AI、Tool 且 Tool ID 相等。

### 17. 无限循环或 GraphRecursionError

- **标准答案**：检查 State 中 model_calls/status 的每步变化、两个 router 返回值和 Graph path map；用 max_steps=1 的工具调用验证应由 ToolNode 正常终止，而不是撞递归上限。
- **真实源码**：`nodes.py::ToolNode` 预算分支；`routing.py`；`services/agent_service.py` recursion config/catch。
- **常见错误回答**：只把 recursion limit 调大。
- **面试表达**：先证明业务终止条件是否失效，再把 recursion limit 当作发现结构错误的信号。
- **追问 1 答案**：若 status 未设 max 而不断回 model，是业务预算失效；若终态正确仍被路由回循环，多半是边或 router 配置错。
- **追问 2 答案**：final/error 后也会进入 tools；最后消息无 calls 时 ToolNode 产生非法响应终态，若路由仍错误则反复触发直到递归错误。

### 18. 跨请求状态污染

- **标准答案**：检查初始 State 是否每次新建列表、compiled graph/node 是否保存可变 messages、是否意外启用全局 checkpointer，以及 service 是否复用上一结果作为输入。
- **真实源码**：`state.py::create_initial_state`；`test_compiled_graph_reuse_does_not_share_state_between_invocations`；API 两请求隔离测试。
- **常见错误回答**：看到同一模型实例就断言一定是 Graph 串状态。
- **面试表达**：区分测试模型的“调用记录”与 Agent State；前者可共享用于断言，后者每次必须独立。
- **追问 1 答案**：检查每次 `received_messages` 的首条 HumanMessage 和长度；如果各自只有本次目标，State 未串，模型记录只是观察器。
- **追问 2 答案**：应用仍可能使用模块级列表、可变默认值、节点实例字段累积消息，或把前次 final State 缓存后复用。

### 19. 稳定错误映射

- **标准答案**：模型调用异常 -> `model_error`；无 tool calls 且空内容 -> `invalid_model_response`；意外 GraphRecursionError -> 稳定 `invalid_model_response` 防御错误。均不暴露堆栈。
- **真实源码**：`nodes.py::ModelNode`；`services/agent_service.py::AgentService.run`。
- **常见错误回答**：所有异常都返回 500，或把 `str(exc)` 全量放进响应。
- **面试表达**：错误按边界稳定化：模型错误在节点，图结构兜底在服务，HTTP 只投影受 Schema 约束结果。
- **追问 1 答案**：堆栈可能包含路径、请求参数、Provider URL 或内部实现，且会使外部契约不稳定。
- **追问 2 答案**：绑定失败发生在任何模型调用之前，所以 steps=0；调用失败已消耗一次尝试，所以 model_calls/steps=1。

### 20. API 的零网络、脱敏与终止证明

- **标准答案**：Application Factory 注入脚本模型；monkeypatch 真实 Provider 构造为立即失败；断言响应不含工具原文、API Key、Base URL query 和 messages；另用 max_steps=1 的工具调用断言返回业务终态与配对执行记录。
- **真实源码**：`tests/integration/test_langgraph_agent_api.py`；P0 `test_health_api.py`。
- **常见错误回答**：只说“用了 fake 所以安全”，或只检查 200。
- **面试表达**：安全性质要用负向断言证明：真实 Provider 一旦被触碰测试就失败，敏感字符串一旦进入响应测试就失败。
- **追问 1 答案**：在 `ChatOpenAI` 构造边界放 monkeypatch，在脚本模型记录处断言消息，在 HTTP 文本上断言秘密/工具正文不存在。
- **追问 2 答案**：200 可能包含泄漏数据、隐藏真实网络调用或错误的 success；必须同时断言状态、结构、调用记录和敏感字符串缺失。
