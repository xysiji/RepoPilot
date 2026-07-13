# RepoPilot P1 面试题答案

## 一、概念题

### 1. Tool Calling 与普通函数调用有什么区别？

- 标准答案：普通函数调用由确定性 Python 代码直接选择函数和参数；Tool Calling 是模型先根据工具 Schema 生成结构化调用意图，再由宿主 Python 校验和执行。模型没有执行本地函数的权限。
- 真实源码：`src/repopilot/agent/loop.py::ToolCallingLoop.run`、`_execute_tool`。
- 常见错误回答：“模型看到函数后会在云端直接运行它。”这混淆了决策与执行边界。
- 面试表达：模型负责提出结构化动作，Python 负责可信执行和结果回填。
- 追问 1 答案：不会。模型只返回工具名、参数和调用 ID，`BaseTool.invoke()` 在 RepoPilot 进程中发生。
- 追问 2 答案：文本格式不稳定、易受提示内容干扰且无法可靠关联结果；标准 `tool_calls` 有明确结构和 Provider 适配。

### 2. `bind_tools()` 承担什么职责？

- 标准答案：它把工具的名称、描述和参数 Schema 交给 Chat Model 的 Provider 适配层，使模型能够返回规范化 `tool_calls`。
- 真实源码：`src/repopilot/agent/loop.py::ToolCallingLoop.run` 中的 `model.bind_tools(list(tools))`。
- 常见错误回答：“调用 bind_tools 后 LangChain 会自动完成工具执行循环。”
- 面试表达：`bind_tools` 只建立模型可见契约，不承担执行器职责。
- 追问 1 答案：它没有调用 `BaseTool.invoke()`，也没有文件权限；真正执行在 `_execute_tool()`。
- 追问 2 答案：`ToolCallingLoop` 追加消息、执行调用，并以无 tool calls 或 max steps 决定终止。

### 3. `AIMessage.tool_calls` 与 `ToolMessage` 如何往返？

- 标准答案：AIMessage 记录模型发出的一个或多个调用；Python 对每个调用执行工具，生成带相同 `tool_call_id` 的 ToolMessage，随后把两类消息一起交回模型。
- 真实源码：`src/repopilot/agent/loop.py::run`、`_execute_tool`。
- 常见错误回答：“工具结果可以作为新的 HumanMessage 发回去。”这会破坏角色与关联语义。
- 面试表达：AIMessage 是请求记录，ToolMessage 是有相关 ID 的执行回执。
- 追问 1 答案：同一工具可被调用多次，甚至在同一 AIMessage 中出现；名称不能唯一标识某一次调用。
- 追问 2 答案：需要。失败 JSON 也是该调用的正式结果，模型可据此修正参数或停止。

### 4. 工具参数 Schema 解决什么问题？

- 标准答案：Schema 明确字段、类型、默认值、长度/数值边界和额外字段策略，同时供模型生成参数和 Python 运行时验证。
- 真实源码：`src/repopilot/schemas/agent.py::ListFilesArgs`、`ReadFileArgs`、`SearchCodeArgs`。
- 常见错误回答：“有类型提示后 Pydantic Schema 就没有必要。”
- 面试表达：Schema 是模型契约与运行时输入防线的交点，但不是全部安全策略。
- 追问 1 答案：不能。Schema 只能证明 path 是字符串；canonical containment、敏感目录和 symlink 边界仍由 `WorkspaceGuard` 验证。
- 追问 2 答案：拒绝多余参数可尽早暴露模型对工具契约的误解，避免静默忽略导致错误自信。

### 5. 四种运行状态表达什么？

- 标准答案：`success` 表示模型给出非空最终答案；`max_steps_exceeded` 表示预算耗尽；`model_error` 表示绑定或调用模型失败；`invalid_model_response` 表示非 AIMessage、缺失调用 ID或空最终文本等协议问题。
- 真实源码：`src/repopilot/schemas/agent.py::AgentRunResult`、`src/repopilot/agent/loop.py`。
- 常见错误回答：“只要某个工具失败，状态就必须立即变成 error。”
- 面试表达：运行状态描述循环如何终止，工具级成功失败由执行记录单独表达。
- 追问 1 答案：不一定。工具失败会回填，模型仍可能纠正后成功回答。
- 追问 2 答案：无调用表示模型声明结束，但没有可交付内容，属于不满足协议的响应。

## 二、源码题

### 6. `ToolCallingLoop.run()` 的顺序是什么？

- 标准答案：校验 goal/max steps/工具唯一性，建立 HumanMessage，绑定工具；逐步调用模型、验证 AIMessage、追加它；无调用则返回答案，有调用则逐个执行并追加 ToolMessage；预算耗尽后返回明确错误。
- 真实源码：`src/repopilot/agent/loop.py::ToolCallingLoop.run`。
- 常见错误回答：“收到 tool calls 后执行第一个，然后马上再次调用模型。”这会丢失同批其他调用。
- 面试表达：循环把协议顺序写成可见代码，每个终止点都有稳定结果。
- 追问 1 答案：字典会覆盖同名工具；提前拒绝重名可避免执行目标不确定。
- 追问 2 答案：一次模型 `invoke()` 是一步；一个步骤内可以执行多个工具调用。

### 7. `_execute_tool()` 如何完成转换？

- 标准答案：按名称取得 BaseTool；未知名称生成失败 JSON，否则 `tool.invoke(tool_input)`；Pydantic 参数错误与普通异常分别转为稳定错误；最后生成同 ID ToolMessage 和执行摘要。
- 真实源码：`src/repopilot/agent/loop.py::_execute_tool`。
- 常见错误回答：“直接调用工具并把返回对象塞进 messages。”这忽略序列化与失败边界。
- 面试表达：一个函数把不可信模型意图转换成受控执行回执。
- 追问 1 答案：`StructuredTool.invoke()` 触发 args Schema 校验，`_execute_tool()` 捕获 `ValidationError`。
- 追问 2 答案：原始异常可能含本地路径或敏感上下文；P1只回传异常类型和稳定描述。

### 8. 多工具调用如何保证全部执行？

- 标准答案：代码遍历完整 `response.tool_calls`，每次追加对应 ToolMessage 和 ToolExecutionRecord，循环结束后才进行下一次模型调用。
- 真实源码：`src/repopilot/agent/loop.py::run` 中的内部 `for tool_call`。
- 常见错误回答：“取 `tool_calls[0]` 就够了，模型通常只会调用一个。”
- 面试表达：模型批量给出的调用是一个有序协议批次，执行器不擅自丢弃。
- 追问 1 答案：严格按模型返回顺序追加，每个结果紧随同批 AIMessage 之后。
- 追问 2 答案：P1 工具少且本地只读，并发会引入调度、错误聚合和顺序语义，当前没有收益证据。

### 9. `WorkspaceGuard` 如何阻止路径逃逸？

- 标准答案：只接收相对路径，显式拒绝绝对路径、drive/root、`..` 和敏感段；随后解析 canonical path 并要求它相对于已解析的 workspace 根。
- 真实源码：`src/repopilot/tools/readonly.py::WorkspaceGuard`。
- 常见错误回答：“只检查字符串是否以 workspace 开头。”这种前缀检查会被相似目录名和链接绕过。
- 面试表达：先拒绝显然危险的语法，再以文件系统解析后的真实路径做包含判断。
- 追问 1 答案：`..` 检查给出清晰 fail-closed 行为；canonical containment 还能防范链接和路径规范化后的越界。
- 追问 2 答案：直接请求经 resolve 后若指向外部会被拒绝；目录遍历与搜索还主动跳过 symlink。

### 10. 脚本模型如何证明离线？

- 标准答案：它继承 `BaseChatModel`，按列表返回预设消息，自行实现不联网的 `bind_tools()`，并记录每次收到的消息；没有 Provider 客户端。
- 真实源码：`tests/scripted_model.py::ScriptedToolCallingModel`。
- 常见错误回答：“配置假 API Key 就等于不会访问网络。”
- 面试表达：测试替身替换调用边界本身，而不是指望远端认证失败。
- 追问 1 答案：P0 Fake 能返回文本，但当前安装版本没有实现 P1 所需的 `bind_tools()` 和完整消息记录。
- 追问 2 答案：`_generate()` 把输入 messages 的副本追加到 `received_messages`，测试检查 AIMessage/ToolMessage 顺序与 ID。

## 三、设计取舍题

### 11. 为什么不用 `create_agent`？

- 标准答案：P1 要直接学习和验证消息追加、工具执行、错误反馈和终止不变量；高层 API 会隐藏这些关键步骤，也可能引入阶段外依赖或行为。
- 真实源码：`src/repopilot/agent/loop.py`；范围记录见 `docs/development/DEVELOPMENT_PLAN.md`。
- 常见错误回答：“高层 API 不好，项目以后永远不能用。”
- 面试表达：当前选择服务于阶段学习目标，不是永久否定框架能力。
- 追问 1 答案：可以逐条断言 tool_call_id、批量调用顺序、失败 JSON 和最大步数，而无需猜框架内部状态。
- 追问 2 答案：当标准工具循环已足够、无需自定义状态/审批/恢复，且团队接受其默认语义时可评估。

### 12. 为什么不用 LangGraph？

- 标准答案：P1 只有单一线性循环，没有审批中断、持久状态或多分支路由；显式循环更小，也满足“先理解协议”的阶段目标。
- 真实源码：`pyproject.toml`、`uv.lock` 和 `src/repopilot/agent/loop.py`；其中均无 LangGraph。
- 常见错误回答：“LangGraph 只能做复杂项目，所以最小循环无法使用。”
- 面试表达：不是不能用，而是当前没有足以抵消依赖和抽象成本的状态图需求。
- 追问 1 答案：后续会需要规划分支、人工审批、重试路由、checkpoint 恢复等。
- 追问 2 答案：AIMessage 必须保留、ToolMessage ID 必须对应、全部调用必须处理、工具失败要可见、循环必须有确定性预算。

### 13. 为什么不用 ToolRegistry？

- 标准答案：三个固定 LangChain Tool 只需运行内名称映射；动态注册、发现、插件生命周期和元数据 Registry 都没有 P1 消费者。
- 真实源码：`src/repopilot/agent/loop.py` 中 `tool_map`；`src/repopilot/tools/readonly.py::build_readonly_tools`。
- 常见错误回答：“Registry 模式本身不好，任何规模都不该用。”
- 面试表达：用最小数据结构满足当前不变量，把扩展框架推迟到有真实变化来源时。
- 追问 1 答案：工具来自多个受控扩展源、需要生命周期/版本/权限元数据且映射重复散落时才值得评估。
- 追问 2 答案：映射前比较 `len(tool_map)` 与工具序列长度，不一致即抛出明确错误。

### 14. 为什么工具错误要回填模型？

- 标准答案：参数错误、文件不存在和未知工具是模型可能纠正的执行反馈。结构化 ToolMessage 保持协议完整，并让下一步基于事实调整。
- 真实源码：`src/repopilot/agent/loop.py::_failure_json`、`_execute_tool`。
- 常见错误回答：“为了让用户看不到错误，所以全部吞掉。”
- 面试表达：错误被转换而非隐藏；模型获得纠错信息，API获得执行摘要。
- 追问 1 答案：缺字段、多字段、未知名称、路径不存在以及工具返回的结构化失败通常可纠正。
- 追问 2 答案：不是。max steps 是 Python 强制预算；模型异常和协议错误还有独立终止状态。

### 15. 为什么 workspace 不由请求提供绝对路径？

- 标准答案：workspace 是服务所有者的信任边界，来自经过校验的应用配置；若用户可指定任意绝对路径，只读工具就可探测服务器文件系统。
- 真实源码：`src/repopilot/infrastructure/config.py::AppSettings.workspace_path`、`src/repopilot/api/routes/agent.py`。
- 常见错误回答：“只读没有副作用，所以读任何目录都安全。”
- 面试表达：读取本身也会泄密，作用域必须由宿主代码而不是用户或模型决定。
- 追问 1 答案：不能；所有工具路径都再次通过 `WorkspaceGuard`，Prompt 中的要求不会改变根目录。
- 追问 2 答案：P1 只做路径、敏感目录和输出上限；P3 才处理完整审批、命令策略和错误分级，不在此提前实现。

## 四、异常与排错题

### 16. 模型反复调用同一工具怎么办？

- 标准答案：每次模型调用消耗一步；固定范围循环在 `max_steps` 后返回 `max_steps_exceeded`，不会依赖模型自觉终止。
- 真实源码：`src/repopilot/agent/loop.py::run` 的 `range(1, max_steps + 1)`。
- 常见错误回答：“在 Prompt 里告诉模型最多重试三次就足够。”
- 面试表达：自然语言负责引导，硬预算由确定性路由执行。
- 追问 1 答案：保留。结果包含 steps、message_count 和此前所有 ToolExecutionRecord。
- 追问 2 答案：模型可能误计数、忽略指令或上下文不完整，不能作为资源安全边界。

### 17. 未知工具名如何处理？

- 标准答案：不执行任何相似工具，创建 `success=false`、`error_type=unknown_tool` 的 ToolMessage，并保持原调用 ID回填。
- 真实源码：`src/repopilot/agent/loop.py::_execute_tool`。
- 常见错误回答：“用编辑距离找最接近的名字自动执行。”这可能执行错误动作。
- 面试表达：未知动作 fail-closed，但把可纠正事实返回模型。
- 追问 1 答案：名称相似不代表语义或权限相同；自动猜测破坏确定性执行边界。
- 追问 2 答案：能看到未知名称的稳定错误类型和消息，以及自己原先的 AIMessage 调用记录。

### 18. `read_file` 如何区分失败？

- 标准答案：路径解析异常映射为 `permission_denied`，缺失为 `not_found`，目录为 `invalid_path`，NUL 或非法 UTF-8 为 `binary_file`，其他文件系统异常为 `filesystem_error`。
- 真实源码：`src/repopilot/tools/readonly.py::read_file`。
- 常见错误回答：“任何异常统一返回 read failed。”这会让模型和排错者失去可行动信息。
- 面试表达：错误类型稳定、细节最小化、结果统一可序列化。
- 追问 1 答案：ToolMessage content 必须跨消息边界传递，JSON 可被模型、日志摘要和测试稳定消费。
- 追问 2 答案：先在字节上限内读取；若截断点落在 UTF-8 尾部最多四字节范围，退回到最后完整字符再解码，最后按字符数截断。

### 19. API 测试访问真实网络如何排查？

- 标准答案：确认 `create_app` 注入了脚本模型，依赖从当前 `app.state` 取得 override，并检查路由仅在 override 为空时调用模型工厂；再对 Provider 发送边界加失败断言。
- 真实源码：`src/repopilot/api/dependencies.py`、`src/repopilot/api/routes/agent.py`、`tests/integration/test_agent_api.py`。
- 常见错误回答：“把 API Key 改成 test 就不会联网。”请求仍可能发出并造成慢测或泄漏。
- 面试表达：离线测试要替换外部边界并验证调用路径，而不是依赖远端失败。
- 追问 1 答案：假 Key 只改变认证结果，不阻止 DNS、连接和 HTTP 请求。
- 追问 2 答案：用同一 App 发两次请求，模型分别记录输入；断言第二次首轮只含新的 HumanMessage，不含第一次 goal 和 ToolMessage。

### 20. 空最终内容如何处理？

- 标准答案：当 AIMessage 无 tool calls 且文本去空白后为空，返回 `invalid_model_response` 和明确错误，不伪造答案。
- 真实源码：`src/repopilot/agent/loop.py::run` 的 final answer 分支。
- 常见错误回答：“HTTP 200 且 final_answer 为空也算成功，交给前端判断。”
- 面试表达：模型声明终止却没有交付内容，是协议层失败而不是业务成功。
- 追问 1 答案：成功状态应保证调用方获得可用答案；否则会把异常推迟成难定位的空 UI。
- 追问 2 答案：同样是 `invalid_model_response`，错误消息指出模型必须返回 AIMessage。
