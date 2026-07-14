# P3：工具安全、策略校验与结构化失败分类

## 1. P3 解决的问题

P2 已有可运行的 LangGraph 工具循环，但 ToolNode 同时承担查找、校验、执行、异常处理和摘要，安全顺序不够显式。P3 把这条路径拆成稳定契约、纯 Policy、统一执行器和只读操作，保证非法参数、策略拒绝和副作用工具都无法到达执行函数。

## 2. 模型为什么不能负责安全裁决

模型是提案者，不是可信执行环境。Prompt 可被任务文本、代码内容或模型错误影响；路径 containment、effect、执行次数和预算必须由确定性 Python 判断并由测试证明。

## 3. 完整六阶段执行顺序

课程把核心顺序概括为参数校验 → 权限判断 → 审批 → 执行 → 分类 → 回填。P3 没有审批，因此实际管线是：Dispatch → Validation → Policy（含 effect/approval 语义）→ Execution → Normalization → ToolMessage/audit 回填。

## 4. Dispatch、Validation、Policy、Execution 的区别

- Dispatch 只判断工具名是否存在。
- Validation 只调用工具真实 Pydantic Schema。
- Policy 只读取已校验参数，判断 effect 和 workspace。
- Execution 最多调用一次真实函数。
- Normalization 验证工具结果 Envelope 并构造协议消息。

## 5. Pydantic Schema 能防什么

`ListFilesArgs`、`ReadFileArgs`、`SearchCodeArgs` 拒绝缺字段、额外字段、类型强制转换、空 query、NUL、超长字符串、超范围深度/结果数和非法后缀。字符串统一 strip。

## 6. Pydantic Schema 不能防什么

Schema 不判断 `.env`、symlink、Junction、ADS、resolve 后越界或工具副作用。把这些塞进字段 validator 会让策略分散，并难以证明三个工具共享同一边界。

## 7. Tool Effect 的用途

`ToolEffect` 是可信代码给工具的副作用标签。生产 mapping 只有三个 `read_only`；模型看不到修改入口，也不能用参数伪造 effect。write/command 在 P3 一律不执行。

## 8. Fail Closed

缺少 effect、Policy 异常、非法结果或需要审批时，系统默认拒绝并返回结构化错误，而不是猜测安全。新增工具若忘记登记 effect，会被测试暴露而不会静默运行。

## 9. PolicyDecision

`ToolPolicyDecision` 同时表达 `allowed`、`effect`、`requires_approval` 和可选 `failure`。Policy 不调用模型、不执行工具、不修改 Graph State；它的输入只有工具名和已校验 Pydantic 对象。

## 10. 成功与失败 Envelope

成功固定为 `success=true, data={...}, error=null`；失败固定为 `success=false, data=null, error={phase,category,code,message}`。`model_validator` 保证 data/error 不会同时存在或同时缺失，`stable_json()` 固定字段次序与紧凑 JSON。

## 11. Phase、Category、Code 的区别

Phase 回答“停在哪一步”；Category 回答“哪类问题”；Code 回答“具体、可稳定断言的原因”。例如 `.env` 是 `policy / policy_denied / sensitive_path_denied`，文件不存在是 `execution / filesystem / not_found`。

## 12. ToolMessage status

当前 `langchain-core` 的 `ToolMessage.status` 支持 `success/error`。执行器按 Envelope 设置状态，并保留模型原始 `tool_call_id`；模型下一轮可同时读取 JSON 分类和标准 status。

## 13. 为什么每个 Tool Call 必须有结果

一个 AIMessage 可能同时产生多个调用。每个调用必须有且只有一个相同 ID 的 ToolMessage，否则 Provider 无法关联请求与观察，同轮协议会失效。

## 14. 工具失败为什么回填模型

文件不存在或策略拒绝是 Agent 可恢复观察，不是整个 HTTP 请求必然失败。模型看到固定 Code 后可换路径；Graph 只在模型终答、模型错误或 max_steps 时终止。

## 15. 为什么不自动重试

P3 每个工具最多执行一次。参数或 Policy 错误重复调用没有意义；未知异常盲目重试会放大资源消耗，并为未来副作用制造重复风险。是否改参数由模型下一轮显式决定，仍受 max_steps 控制。

## 16. 为什么 Policy 拒绝不是 HTTP 403

HTTP 请求本身有权运行 Agent，失败的是 Agent 内部某次工具提案。它被记录为 ToolMessage，模型可能恢复；只有整个 AgentRun 的最终状态决定 API 响应内容。

## 17. Workspace 安全链路

Schema 先清理基本输入；Policy 的 `WorkspaceGuard.check()` 处理词法规则、链接和 containment；执行函数用 `resolve_existing()` 再次检查；遍历发现的每个条目也走同一 Guard。API 只得到相对路径摘要。

## 18. ADS 漏洞的成因与修复

Windows NTFS 可用 `file::$DATA` 访问数据流。若只按文件名精确匹配 `.env`，别名可能绕过。P3 在路径解析前拒绝冒号，因此 `.env::$DATA` 在 Policy 阶段稳定得到 `sensitive_path_denied`。

## 19. Symlink/Junction 风险

词法上位于 workspace 的路径可以通过链接指到外部。P3 遍历每个已有路径段，拒绝 Symlink/Junction，并在 resolve 后再次 containment。当前全拒链接换取可证明边界；仍不能消除检查与打开之间的所有竞态。

## 20. 敏感文件规则

规则大小写不敏感，覆盖 `.env*`、`.git`、`.venv`、`__pycache__`、`id_rsa*`、`id_ed25519*`、`.pem`、`.key`，并按每个路径段执行。规则保持有限，避免误伤普通源代码。

## 21. 资源上限

系统常量集中在 `ToolLimits`。模型只能请求更小的 max_depth/max_results。list/read/search 的既有可截断场景仍是成功并标记 `truncated=true`；不可安全截断的工具可使用 `resource_limit_exceeded`。

## 22. 安全日志和参数脱敏

`ToolExecutionRecord.input` 只保存字段名列表，不保存值。错误消息不拼接 `str(exc)`，ValidationError 只提取字段位置和错误类型且 `include_input=False`。审计不保存文件正文、API Key、Base URL、外部绝对路径或 traceback。

## 23. P2 Tool Node 与 P3 Safe Executor 对照

P2 ToolNode 自己维护工具字典并捕获异常；P3 ToolNode 只读取最后一个 AIMessage、按顺序调用执行器、收集消息和记录。安全规则因此可脱离 Graph 单测，Graph 拓扑保持不变。

## 24. 与 KamaClaude S5 的差异

共同点是参数先于权限、失败回填模型、未知能力保守处理。RepoPilot 使用 LangChain Schema/ToolMessage 和 LangGraph State，不复制 ToolRegistry、EventBus、daemon、Future、TUI、always cache 或自动重试；P3 也没有 bash/write 工具。

## 25. 为什么当前不是沙箱

Agent 和宿主 FastAPI 进程权限相同。Policy 降低误访问和提示注入风险，但不能抵抗进程被攻破、恶意依赖、内核漏洞或所有 TOCTOU。强隔离需要操作系统沙箱、容器或独立低权限进程。

## 26. 为什么 P3 不使用 interrupt

生产工具全部只读；write/command 已在 Policy 阶段拒绝。没有需要暂停等待的合法副作用动作，因此引入 interrupt/checkpoint 只会提前制造 P4 状态语义。

## 27. P4 将如何插入 Approval

P4 应在 Validation/静态 Policy 之后、Execution 之前加入批准步骤，并把批准绑定到具体 Patch hash、preimage 和 workspace 状态。恢复后必须重新复核，不能仅批准工具名。

## 28. 每个新增文件职责

- `tools/contracts.py`：Schema、effect、失败三维类型、Envelope、审计和系统上限。
- `tools/policy.py`：纯 effect/workspace 裁决与统一 Guard。
- `tools/executor.py`：严格顺序、一次执行、异常分类、Normalization、ToolMessage/audit。
- `docs/security/TOOL_SAFETY_POLICY.md`：威胁模型和可验证边界。
- `scripts/demo_p3.py`：离线演示拒绝、恢复和成功。
- 三个 P3 单测文件：分别证明契约、策略和执行顺序。

## 三个完整例子

### 非法参数

```text
read_file({path: 42, extra: "x"})
→ dispatch 命中
→ validation
→ invalid_arguments
→ Policy Spy 调用次数为 0
→ 工具计数器为 0
→ error ToolMessage 回填
```

### 读取敏感文件

```text
read_file({path: ".env"})
→ validation 通过
→ policy 拒绝
→ policy_denied / sensitive_path_denied
→ 工具不执行
→ error ToolMessage 回填
→ 模型可以选择 README.md
```

### 读取安全文件

```text
read_file({path: "README.md"})
→ validation 通过
→ policy 允许 read_only
→ execution 一次
→ normalization 验证 Envelope
→ success ToolMessage
```

## 29. 主动修改练习

设计 `restricted_read_file`，但默认不进入生产代码：Schema 仍只校验严格 path 字符串；Policy 先复用 WorkspaceGuard，再只允许最终安全相对路径后缀 `.py/.md`。不能只看后缀，因为 `../x.md`、symlink 和 `.md::$DATA` 仍可越界或形成别名。用计数器证明拒绝时函数执行次数为 0，并覆盖 extra、ADS、大小写、链接、无后缀、允许后执行一次和 Envelope。

## 30. 故障注入练习

| 故障 | 表现/风险 | 定位方法 |
| --- | --- | --- |
| Policy 放到执行后 | `.env` 已被读取才拒绝 | 工具计数器应为 0 |
| 缺 effect 默认允许 | 新工具静默运行 | unknown effect 单测 |
| 原样返回 ValidationError | input/秘密进入模型/API | 搜索 `input_value` 和秘密 marker |
| 原样返回异常字符串 | 绝对路径/密钥泄漏 | 注入带秘密异常并断言响应 |
| Policy 拒绝仍执行 | 产生实际副作用 | write/command counter |
| 漏一个 ToolMessage | Provider 协议断裂 | 数量和 ID 一一对应断言 |
| Tool Call ID 写错 | 结果归属错误 | 原始 ID 精确比较 |
| 首个失败阻止后续调用 | 同轮证据丢失 | 混合失败/成功顺序测试 |
| 审计存完整文件 | API 泄漏源码 | 内容 marker 不应出现 |
| `.env::$DATA` 可读 | Windows 秘密绕过 | ADS 回归测试 |
| 自动重试永久失败 | 放大资源/未来副作用 | 调用次数必须为 1 |
| 策略交给 Prompt | 无法确定性证明 | 删除 Prompt 后代码测试仍应通过 |

## 必须亲手复写（合计约 190 行）

1. `tools/contracts.py` 中三个 Pydantic 参数 Schema（约 45 行）。
2. `tools/policy.py` 中 `ToolSafetyPolicy.evaluate()` 与路径关键检查（约 55 行）。
3. `tools/executor.py` 中 Dispatch/Validation/Policy/Execution 顺序骨架（约 60 行）。
4. `tools/executor.py` 中 Exception → ToolFailure → ToolMessage/audit（约 30 行）。

复写时先写不变量和 Spy 测试，再写实现；不要照抄完整文件。

## 31. 一分钟面试口述稿

“P3 没有增加工具，而是把 P2 的三只读工具放进统一安全执行管线。模型请求先按名称分派，再用工具真实 Pydantic Schema 校验；参数错不会进入 Policy。Policy 用 Python mapping 固定 Tool Effect，未知、write、command 都 fail closed，并用共享 WorkspaceGuard 拒绝穿越、ADS、设备名、敏感文件和链接。通过后工具最多执行一次，结果必须符合固定 Envelope，异常只转成安全 Phase、Category、Code。每个调用都保留原 Tool Call ID，生成带 status 的 ToolMessage 和脱敏审计，失败回填模型而不是直接 HTTP 403。Graph 拓扑没变，也没有 interrupt、重试或写能力。这个 Policy 降低误访问风险，但宿主权限相同，所以不是操作系统沙箱。”
