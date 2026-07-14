# P3 面试题：工具安全与失败分类

## 一、概念题（1–5）

### 1. 参数 Schema 与 Tool Policy 的职责边界是什么？

- 追问 A：为什么 `.env` 不应由 Pydantic validator 直接裁决？
- 追问 B：怎样证明非法参数没有进入 Policy？

### 2. 什么是 Tool Effect，为什么必须由可信 Python 代码定义？

- 追问 A：模型在参数中传 `effect=read_only` 会怎样？
- 追问 B：新增工具忘记登记 Effect 应采用什么默认行为？

### 3. Fail Closed 在 P3 中体现在哪些分支？

- 追问 A：Policy 自身抛异常时为什么不能默认允许？
- 追问 B：write/command 为什么不是“先运行再记录风险”？

### 4. Phase、Category 和 Code 为什么要拆成三维？

- 追问 A：`.env` 和不存在文件分别如何分类？
- 追问 B：哪一维适合稳定测试断言？

### 5. 为什么工具失败仍要生成 ToolMessage 回填模型？

- 追问 A：Policy 拒绝为什么不是 HTTP 403？
- 追问 B：为什么不自动重试同一个失败调用？

## 二、源码题（6–10）

### 6. 从 AIMessage.tool_calls 到下一轮模型，源码执行链是什么？

- 追问 A：同轮多调用如何保证顺序？
- 追问 B：模型预算耗尽时最后一批 ToolMessage 会丢吗？

### 7. SafeToolExecutor 如何保证 Validation 失败后不调用 Policy 和工具？

- 追问 A：ValidationError 怎样脱敏？
- 追问 B：为什么使用工具真实 `get_input_schema()`？

### 8. WorkspaceGuard 如何防路径穿越和 resolve 后越界？

- 追问 A：只过滤 `..` 为什么不够？
- 追问 B：Windows 大小写和分隔符差异如何处理？

### 9. 统一 Envelope 如何保证成功/失败形状互斥且 JSON 稳定？

- 追问 A：工具返回普通字符串会怎样？
- 追问 B：ToolMessage.status 在哪里设置？

### 10. ToolExecutionRecord 如何兼容旧字段并避免泄漏？

- 追问 A：为什么 input 只保存字段名？
- 追问 B：成功记录的失败字段应是什么？

## 三、设计取舍题（11–15）

### 11. 为什么 P3 自定义 SafeToolExecutor，而不把安全逻辑继续放在 ToolNode？

- 追问 A：为什么没有增加 Policy Node？
- 追问 B：Graph 拓扑保持不变有什么价值？

### 12. 为什么采用小型 Effect mapping，而不是 RBAC、插件 Registry 或策略 DSL？

- 追问 A：这种设计放弃了什么灵活性？
- 追问 B：什么时候才值得引入更通用策略？

### 13. 为什么 P3 全拒 Symlink/Junction，即使链接目标在 workspace 内？

- 追问 A：这牺牲了什么可用性？
- 追问 B：执行前复核能完全消除 TOCTOU 吗？

### 14. 为什么 read/list/search 的安全截断仍算成功？

- 追问 A：什么时候应返回 `resource_limit_exceeded`？
- 追问 B：模型为什么不能请求自动扩大上限？

### 15. 为什么 P3 不使用 interrupt，也不实现人工审批？

- 追问 A：P4 的 Approval 应插入在哪里？
- 追问 B：批准工具名为什么不足以授权 Patch？

## 四、安全与排错题（16–20）

### 16. `.env::$DATA` 为什么可能绕过简单文件名判断，P3 如何修复？

- 追问 A：哪些其他 Windows 路径别名被拒绝？
- 追问 B：如何写不泄漏秘密的 ADS 回归测试？

### 17. 如何证明 Policy 拒绝后真实工具函数没有执行？

- 追问 A：只断言错误 Code 为什么不够？
- 追问 B：如何对 write/command synthetic tool 做故障注入？

### 18. 工具抛出包含 API Key 和外部绝对路径的异常时，系统应怎样处理？

- 追问 A：为什么不能直接使用 `str(exc)`？
- 追问 B：怎样区分预期 filesystem 异常和未知异常？

### 19. 同轮第一个工具失败后第二个工具没有执行，如何定位？

- 追问 A：哪些协议断言能发现漏消息或错 ID？
- 追问 B：错误工具结果结构非法时怎样保证整轮不崩？

### 20. 为什么 P3 Policy 不是沙箱，安全边界还缺什么？

- 追问 A：宿主进程被攻破时 Policy 是否有效？
- 追问 B：若未来执行 pytest 或 Patch，还需哪些额外边界？
