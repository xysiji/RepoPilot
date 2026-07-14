# P1：最小 Tool Calling Agent

> 历史阶段说明：本文件记录 P1 手写循环的学习基线。该生产实现已在 P2 删除并迁移到 LangGraph StateGraph；可在 Git 提交 `aa39eeb` 查看 P1 源码。当前生产路径请以 `docs/learning/P2-langgraph-stategraph.md` 和 `src/repopilot/agent/` 为准。

## 1. P1 解决的问题

P1 在不使用 LangGraph、`create_agent` 或 `AgentExecutor` 的前提下，跑通最小且显式的模型工具调用闭环。模型负责决定是否调用只读工具，Python 负责校验参数、执行工具、生成 `ToolMessage`、保持调用 ID，并通过最大步数确定性终止。整个默认演示与测试使用脚本模型，不访问真实模型网络。

## 2. 一次真实请求的完整执行链路

1. `POST /agent/run` 由 `api/routes/agent.py::run_agent` 接收目标和最大步数。
2. FastAPI 依赖从当前 App 实例取得 `AppSettings` 与可选模型替身。
3. 没有替身时才由 P0 模型工厂构造真实模型；配置不完整则返回稳定的 503。
4. `AgentService.run()` 为本次请求新建三个只读工具与 `ToolCallingLoop`。
5. 循环用目标创建 `HumanMessage`，调用 `model.bind_tools(tools)`。
6. 模型返回带 `tool_calls` 的 `AIMessage`；循环先保存它，再按顺序执行每个调用。
7. Python 通过工具名映射找到工具，以结构化参数调用，并把 JSON 结果放入同 ID 的 `ToolMessage`。
8. 更新后的 messages 再次交给模型；模型不再请求工具时，其文本成为最终答案。
9. API 只返回结果、步数和工具执行摘要，不返回完整消息历史或完整工具内容。

## 3. `bind_tools()` 做了什么、不做什么

`bind_tools()` 把工具名、描述和参数 Schema 绑定到 Chat Model，使 Provider 能把这些定义作为可选工具告诉模型，并把 Provider 响应规范化为 `AIMessage.tool_calls`。它不执行 Python 函数，不校验 workspace 边界，不追加 `ToolMessage`，也不负责循环或终止；这些仍由 `ToolCallingLoop` 和工具实现承担。

## 4. 模型如何生成 `tool_calls`

模型接收目标、历史消息及工具 Schema 后，可以返回普通文本，也可以返回一个或多个结构化调用。LangChain 将调用规范化为至少包含 `name`、`args`、`id` 和类型信息的字典。RepoPilot 不解析模型文本来猜工具调用，只读取 `AIMessage.tool_calls`。

## 5. Python 如何找到并执行工具

循环在运行开始时把 `Sequence[BaseTool]` 转成 `{tool.name: tool}`，同时拒绝重名。每个 tool call 按 `name` 查找，调用 `tool.invoke(args)`。Pydantic Schema 拒绝缺失、多余或越界参数；未知名称、参数错误和运行时异常都被转换成稳定 JSON，再作为工具失败回填模型。

## 6. 为什么必须保留产生调用的 `AIMessage`

工具结果不是独立的新问题，而是对某个模型调用的响应。必须先追加原始 `AIMessage`，再追加对应 `ToolMessage`，模型才能看到“我请求了什么”和“Python 返回了什么”。丢失 AIMessage 会破坏消息协议，也会让多工具调用失去上下文。

## 7. `ToolMessage` 为什么必须携带 `tool_call_id`

同一 `AIMessage` 可以请求多个工具，名称也可能相同。`tool_call_id` 是结果与原请求的唯一关联键。RepoPilot 原样复制模型给出的非空 ID；若 ID 缺失，返回 `invalid_model_response`，不会自行猜测或生成替代 ID。

## 8. 多工具调用如何处理

循环遍历当前 `AIMessage.tool_calls` 的完整列表，严格保持模型给出的顺序。每次调用都生成独立 `ToolMessage` 和 `ToolExecutionRecord`，全部处理后才进入下一次模型调用。P1 采用顺序执行，避免为三个本地只读工具提前引入并发、调度或事件总线。

## 9. messages 每一步如何变化

```text
HumanMessage("总结 sample_project/README.md")
AIMessage(tool_calls=[{"name": "list_files", "id": "call-1", ...}])
ToolMessage(tool_call_id="call-1", content="{...}")
AIMessage(tool_calls=[{"name": "read_file", "id": "call-2", ...}])
ToolMessage(tool_call_id="call-2", content="{...}")
AIMessage("最终摘要")
```

工具失败时消息形状不变：失败仍是具有相同 `tool_call_id` 的 `ToolMessage`，只是 content 是 `success=false` 的结构化 JSON，状态为 error。

## 10. 最大步数如何终止循环

一次模型 `invoke()` 计为一步。循环只遍历 `1..max_steps`；若最后一步仍产生工具调用，工具结果会被保存，然后返回 `max_steps_exceeded`，不再进行下一次模型调用。结果保留已发生的步数、消息数量和工具执行摘要，因此终止可解释且不会无限循环。

## 11. 为什么工具失败要回填模型

不存在文件、参数不完整或未知工具通常是模型可以纠正的运行信息。把它作为 `ToolMessage` 回填，模型可改用合法参数或给出解释；直接让异常击穿 API 会丢失这个纠错机会。P1 不自动决定重试次数以外的复杂策略，也不会吞掉错误：API 结果仍保留失败类型和摘要。

## 12. 与 KamaClaude S1 AgentLoop 的对照

定向阅读 S1 后保留了“模型—工具—结果回填—再次调用—明确终止”的核心问题，以及工具错误不应直接破坏循环的原则。RepoPilot 使用 LangChain 的 `AIMessage.tool_calls`、`ToolMessage` 和 `BaseTool` 重新实现，不迁移 Anthropic 原生 content block 解析、daemon 生命周期、EventBus、EventWriter、会话或取消传播体系。

## 13. 为什么没有复制 ToolRegistry 和 EventBus

P1 只有三个固定工具，一个运行内的字典已经能检查唯一名称和完成查找。通用 Registry、插件发现和动态注册没有当前消费者。执行也是同步请求内闭环，没有其他客户端订阅事件，所以自定义 EventBus 只会增加状态和测试面。

## 14. 为什么 P1 不使用 LangGraph

本阶段的学习目标是亲手理解消息如何变化、工具结果如何关联、错误如何回填以及循环如何停止。约百行显式循环能直接展示这些不变量。LangGraph 留给后续真正需要条件路由、审批中断和状态恢复的阶段；P1 既未依赖也未导入它。

## 15. 每个新增或修改文件的职责

| 文件 | 职责 |
| --- | --- |
| `src/repopilot/schemas/agent.py` | 工具参数/结果、执行记录、运行请求与响应契约 |
| `src/repopilot/tools/readonly.py` | workspace 边界及三个 LangChain 只读工具 |
| `src/repopilot/agent/loop.py` | 显式 Tool Calling 循环、错误回填与终止 |
| `src/repopilot/services/agent_service.py` | 每次运行的新工具和新消息组合 |
| `src/repopilot/api/routes/agent.py` | `/agent/run` HTTP 边界与稳定配置错误 |
| `src/repopilot/api/app.py` | 在既有 Application Factory 中注册 Agent Router |
| `src/repopilot/infrastructure/config.py` | 增加应用控制的 workspace 路径配置 |
| `tests/scripted_model.py` | 仅测试使用的脚本化 `BaseChatModel` |
| `tests/unit/test_readonly_tools.py` | 路径、敏感目录、编码、截断和搜索边界 |
| `tests/unit/test_tool_calling_loop.py` | 消息协议、异常反馈、多调用和终止 |
| `tests/integration/test_agent_api.py` | 离线 HTTP 闭环、Health 回归和请求隔离 |
| `scripts/demo_p1.py` | list → read → answer 的离线演示 |
| `demo_workspace/sample_project/*` | 演示所需的最小只读输入 |

## 16. 必须亲手复写的三段关键代码

总复写量建议控制在 100—180 行，并在不看源码时完成：

1. 复写一个带 Pydantic 参数 Schema、结构化 JSON 结果和 workspace 相对路径检查的 `read_file` Tool（约 45—70 行）。
2. 复写 Tool Call → 名称查找 → `tool.invoke()` → 同 ID `ToolMessage` 的转换，并覆盖参数错误与工具异常（约 40—65 行）。
3. 复写“无 tool calls 成功结束”和“达到 max steps 失败结束”的两个判断（约 15—30 行）。

复写后必须用缺参数、多余参数、未知工具、重复工具调用和空最终答案验证，不要只跑成功路径。

## 17. 主动修改练习

为 `search_code` 增加可选的大小写不敏感开关。先修改参数 Schema，再修改匹配逻辑、工具描述和测试。要求默认行为不变，结果顺序仍稳定，不引入正则表达式或外部搜索命令。完成后解释为什么该开关由确定性 Python 执行，而不是让模型在提示词中“假装忽略大小写”。

## 18. 故障注入练习

让脚本模型连续返回 3 次同名 `read_file`，其中第一次路径不存在、第二次缺少 path、第三次合法；观察每一步 `AIMessage` 与 `ToolMessage` 的排列和 ID。然后把 `max_steps` 设为 2，确认第三次不会发生且结果为 `max_steps_exceeded`。最后让工具函数抛出 `RuntimeError`，确认 API 不崩溃且模型收到 `tool_exception`。

## 19. 一分钟面试口述稿

“RepoPilot P1 没用 LangGraph，而是显式实现最小 Tool Calling 循环。请求进入后创建 HumanMessage，模型通过 bind_tools 获得三个只读工具的 Schema。模型返回 AIMessage.tool_calls 时，Python 保留这条 AIMessage，按顺序用名称映射执行全部工具，再把稳定 JSON 包成具有相同 tool_call_id 的 ToolMessage 回填。未知工具、参数错误和工具异常不会击穿请求，而是成为模型可纠正的信息；模型无调用时结束，超过最大模型步数则确定性返回 max_steps_exceeded。文件工具只接受 workspace 内相对路径，排除敏感目录并限制输出。测试使用脚本化 BaseChatModel，完整 API 和 Demo 都离线。这样 P1 展示了真正的消息协议与终止条件，同时没有提前引入写文件、审批、规划、持久化或 LangGraph。”
