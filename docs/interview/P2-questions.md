# P2 面试题：LangGraph StateGraph

## 一、概念题（1–5）

### 1. StateGraph 中 State、Node、Edge、START 和 END 分别承担什么职责？

- 追问 1：为什么节点应返回局部 State update，而不是每次复制完整 State？
- 追问 2：RepoPilot P2 的实际节点和边有哪些？

### 2. reducer 是什么？`add_messages` 与普通列表覆盖有什么区别？

- 追问 1：为什么 `tool_executions` 使用 `operator.add`，控制字段却不用 reducer？
- 追问 2：如果去掉 messages reducer，哪个测试最先暴露问题？

### 3. `model_calls`、`max_steps` 与 LangGraph recursion limit 有什么区别？

- 追问 1：同一 AIMessage 调用三个工具，三个计数分别怎样变化？
- 追问 2：为什么不能用 `GraphRecursionError` 作为正常的 max_steps 终止？

### 4. Tool Calling 为什么必须保持 AIMessage、ToolMessage 和 tool_call_id 的协议完整？

- 追问 1：同轮多个 tool calls 应按什么顺序执行和回填？
- 追问 2：其中一个工具失败时，为什么仍要给其余调用生成 ToolMessage？

### 5. compiled graph、Checkpointer 和一次 invoke 的 State 是什么关系？

- 追问 1：为什么同一 compiled graph 在 P2 可以跨请求复用却不会记住上次消息？
- 追问 2：如果未来启用 Checkpointer，还需要新增哪些调用约束？

## 二、源码题（6–10）

### 6. 请从源码定位 P2 Graph Builder，并完整描述图是如何编译出来的。

- 追问 1：工具重名在哪个位置被拒绝？
- 追问 2：哪个测试证明节点名和无 Checkpointer？

### 7. ModelNode 如何处理 tool calls、直接回答、空回答和模型异常？

- 追问 1：`model_calls` 在模型异常时是否增加，为什么？
- 追问 2：ModelNode 如何证明自己没有执行工具？

### 8. 自定义 ToolNode 如何保证同轮多工具的顺序、ID 和稳定错误？

- 追问 1：参数校验失败与工具内部异常分别映射为什么 error_type？
- 追问 2：达到 max_steps 时为什么先执行完本批工具？

### 9. 两个路由函数的完整逻辑是什么，为什么说它们是纯函数？

- 追问 1：running 但最后消息无 tool calls 时怎样处理？
- 追问 2：path map 对图拓扑和可视化有什么价值？

### 10. 从 `POST /agent/run` 到 `AgentRunResult` 的调用链是什么？

- 追问 1：未配置模型时在哪一层返回 503？
- 追问 2：为什么 API 不直接返回最终 AgentState？

## 三、设计取舍题（11–15）

### 11. P2 为什么把 P1 普通循环迁移到 Graph，而不是继续扩展 `for` 循环？

- 追问 1：普通循环仍有哪些优势？
- 追问 2：P2 为显式控制流付出了什么成本？

### 12. 为什么 P2 使用自定义 ToolNode，而不直接使用 `langgraph.prebuilt.ToolNode`？

- 追问 1：当前自定义节点保留了哪四项项目语义？
- 追问 2：什么条件下值得重新评估预构建 ToolNode？

### 13. 为什么不用 `create_agent`、AgentExecutor 或 LangGraph Functional API？

- 追问 1：Graph API 对学习和测试的直接收益是什么？
- 追问 2：Functional API 在什么场景可能更合适？

### 14. 为什么 P2 不启用 Checkpointer、interrupt 和 thread_id？

- 追问 1：放弃这些能力意味着什么？
- 追问 2：为什么仅用内存 Checkpointer 做演示也会扩大阶段范围？

### 15. 为什么模型、工具、Settings 和 WorkspaceGuard 不进入 AgentState？

- 追问 1：这些依赖现在如何进入节点？
- 追问 2：若把 API Key 放进 State，会产生哪些具体风险？

## 四、异常与排错题（16–20）

### 16. 下一轮模型看不到 ToolMessage，或者 tool_call_id 不匹配，应如何定位？

- 追问 1：先检查 reducer、ToolNode 还是路由，为什么？
- 追问 2：如何用脚本模型构造最小回归测试？

### 17. Graph 出现无限循环或意外触发 GraphRecursionError，应如何排查？

- 追问 1：如何区分业务 max_steps 失效与图边配置错误？
- 追问 2：`route_after_model` 永远返回 tools 会出现什么症状？

### 18. 两次 `/agent/run` 请求出现消息串线，最可能的原因有哪些？

- 追问 1：如何证明问题来自 State，而不是脚本模型的测试记录？
- 追问 2：为什么无 Checkpointer 仍可能因应用代码写错而串状态？

### 19. 模型异常、空最终回答和防御性 recursion error 分别怎样映射为稳定结果？

- 追问 1：为什么不能把内部堆栈放进 API？
- 追问 2：模型绑定阶段失败与模型调用阶段失败的 steps 为什么不同？

### 20. 如何证明 P2 API 没有真实网络调用、没有泄露消息/工具全文/密钥，且 max_steps 正常终止？

- 追问 1：应在哪些边界放置失败即报错的 monkeypatch 或断言？
- 追问 2：为什么只断言 HTTP 200 不足以证明这些安全性质？
