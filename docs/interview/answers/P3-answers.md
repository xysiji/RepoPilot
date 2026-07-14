# P3 面试题答案：工具安全与失败分类

## 1. Schema 与 Policy

**标准答案**：Schema 负责结构、类型、长度、范围和额外字段；Policy 只接收已校验对象，负责 effect、workspace、敏感路径和链接。
**源码位置**：`src/repopilot/tools/contracts.py::ToolArgsModel/ListFilesArgs/ReadFileArgs/SearchCodeArgs`；`src/repopilot/tools/policy.py::ToolSafetyPolicy.evaluate`。
**常见错误回答**：“全部放进 Pydantic 就安全。”这会混合输入格式与授权语义。
**面试表达**：“先证明输入合法，再决定动作是否被允许，两层失败对象和测试证据不同。”
**追问 A**：`.env` 是项目安全策略，多个工具应共享，不能散落在字段 validator。
**追问 B**：`test_validation_failure_calls_neither_policy_nor_tool_and_hides_input` 用 Policy/工具计数器都等于 0 证明。

## 2. Tool Effect

**标准答案**：Effect 是可信代码赋予工具的副作用分类，生产 mapping 只有三个 read_only；模型不能提交或修改它。
**源码位置**：`contracts.py::ToolEffect`；`policy.py::PRODUCTION_TOOL_EFFECTS`。
**常见错误回答**：“让模型按描述判断是否只读。”模型输出不是授权依据。
**面试表达**：“模型提议调用，Python effect mapping 决定能否进入执行。”
**追问 A**：`effect` 是额外参数，会在 Validation 阶段被 `extra="forbid"` 拒绝。
**追问 B**：缺分类返回 `unclassified_tool_effect`，fail closed。

## 3. Fail Closed

**标准答案**：未知工具、未知 effect、write/command、Policy 异常、需要审批和非法结果都拒绝并产生 ToolMessage。
**源码位置**：`tools/executor.py::SafeToolExecutor.execute`；`tools/policy.py::ToolSafetyPolicy.evaluate`。
**常见错误回答**：“异常时先放行，避免影响可用性。”这会把安全失败变成执行许可。
**面试表达**：“无法得到可信允许结论，就返回稳定拒绝；失败仍保持协议完整。”
**追问 A**：Policy 异常返回 internal failure/unclassified code，不执行工具。
**追问 B**：副作用必须先拒绝，运行后记录已经无法撤销影响。

## 4. 三维失败分类

**标准答案**：Phase 是失败位置，Category 是聚合族，Code 是具体稳定原因；三者避免一个自由字符串同时承担流程、指标和恢复语义。
**源码位置**：`contracts.py::ToolExecutionPhase/ToolFailureCategory/ToolErrorCode/ToolFailure`。
**常见错误回答**：“一个 error_type 就够。”它无法区分同一 Code 的发生阶段或聚合不同文件错误。
**面试表达**：“Phase 用来定位管线，Category 用来统计，Code 用来断言和让模型恢复。”
**追问 A**：`.env` 为 policy/policy_denied/sensitive_path_denied；不存在为 execution/filesystem/not_found。
**追问 B**：Code 最适合稳定测试，不能断言易变的自然语言消息。

## 5. 失败回填

**标准答案**：工具错误是一次可恢复观察，每个 Tool Call 必须有同 ID 的 ToolMessage；模型可改路径，Graph 由终答或 max_steps 决定终止。
**源码位置**：`executor.py::_finalize`；`agent/nodes.py::ToolNode.__call__`。
**常见错误回答**：“任何拒绝都终止请求并返回 403。”这破坏 Agent 恢复语义。
**面试表达**：“拒绝的是内部动作，不是用户调用 Agent API 的身份权限。”
**追问 A**：因此 Policy 拒绝回填模型，最终 HTTP 仍按 AgentRun 状态响应。
**追问 B**：不自动重试；模型显式给新参数且仍受模型轮次预算。

## 6. 消息执行链

**标准答案**：ModelNode 追加 AIMessage；router 到 ToolNode；ToolNode 按 tool_calls 顺序调用 SafeToolExecutor；返回 ToolMessage/record reducer；router 回 ModelNode。
**源码位置**：`agent/nodes.py`、`agent/routing.py`、`agent/graph.py`。
**常见错误回答**：“工具执行后才把 AIMessage 放历史。”这会打乱协议顺序。
**面试表达**：“先 observe AIMessage，再 act；每个调用结果按原顺序追加。”
**追问 A**：普通 for 循环保持模型顺序，不并发。
**追问 B**：ToolNode 完成整批后才设置 max_steps_exceeded，所以最后一批不丢。

## 7. Validation 顺序

**标准答案**：执行器查到工具后调用 `get_input_schema().model_validate()`；ValidationError 立即 finalize，不触发 Policy 或 invoke。
**源码位置**：`tools/executor.py::SafeToolExecutor.execute/_safe_validation_locations`。
**常见错误回答**：“先 Policy 后校验也一样。”Policy 会接触未可信类型并可能误判。
**面试表达**：“参数错是给 Agent 的纠错信号，不是授权问题。”
**追问 A**：调用 `errors(include_input=False, include_context=False, include_url=False)`，只保留 loc/type。
**追问 B**：真实 schema 与模型看到/工具 invoke 的合同一致，避免维护第二套参数模型。

## 8. WorkspaceGuard

**标准答案**：先统一分隔符并拒绝父级/绝对/盘符/UNC/设备/ADS/敏感段，再检查链接，resolve，最后用 normcase/commonpath containment。
**源码位置**：`tools/policy.py::WorkspaceGuard`。
**常见错误回答**：“只要没有 `..` 就不会越界。”绝对路径、链接和别名仍可绕过。
**面试表达**：“词法拒绝处理别名，canonical resolve 处理真实目标，执行前再复核。”
**追问 A**：Symlink/Junction、盘符和 ADS 都不依赖 `..`。
**追问 B**：先把 `\` 转 `/` 做一致分段；containment 使用 `os.path.normcase/commonpath`。

## 9. Envelope 与 Normalization

**标准答案**：Pydantic model_validator 保证 success 与 data/error 互斥；stable_json 固定字段次序。Normalization 只接受 Envelope、合法 JSON 或 mapping。
**源码位置**：`contracts.py::ToolResultEnvelope`；`executor.py::_normalize_result`。
**常见错误回答**：“任何字符串都当成功 observation。”这会绕过统一失败合同。
**面试表达**：“工具实现也不被盲信，执行后还有输出合同检查。”
**追问 A**：普通字符串转 `normalization/internal_failure/invalid_tool_result`。
**追问 B**：`_finalize` 根据 envelope.success 设置 ToolMessage status。

## 10. ToolExecutionRecord

**标准答案**：保留 step/name/id/success/summary 和旧 error_type/message，新增 phase/category/code/effect/policy_allowed；input 只列字段名。
**源码位置**：`contracts.py::ToolExecutionRecord`；`executor.py::_finalize`。
**常见错误回答**：“审计应保存完整参数和输出便于排错。”这会通过 API 泄漏代码和秘密。
**面试表达**：“审计保留决策事实，不复制敏感载荷。”
**追问 A**：字段名足以说明调用形状，值已在 ToolMessage/工作区中按需存在。
**追问 B**：成功时 category/code/error_type/error_message 均为空，phase 为 normalization 完成。

## 11. 为什么独立 SafeToolExecutor

**标准答案**：执行顺序和安全分类可脱离 Graph 单测；ToolNode 只负责协议批处理，避免继续堆逻辑。
**源码位置**：`tools/executor.py` 与 `agent/nodes.py::ToolNode`。
**常见错误回答**：“多一个类只是企业级分层。”这里的边界对应可独立证明的安全顺序。
**面试表达**：“Graph 管编排，Executor 管单次调用安全事务。”
**追问 A**：不增 Policy Node，避免改变 P2 拓扑和把每次工具内部检查膨胀为 Graph 状态。
**追问 B**：拓扑测试不回归，P4 前仍是最小模型—工具闭环。

## 12. 小型 mapping 取舍

**标准答案**：当前只有三个生产工具，显式 dict 最容易审计和 fail closed；RBAC/DSL/插件没有真实需求。
**源码位置**：`policy.py::PRODUCTION_TOOL_EFFECTS`。
**常见错误回答**：“先做通用策略引擎方便未来扩展。”会增加未验证分支和配置绕过面。
**面试表达**：“固定能力集合优先用可枚举代码，不为未来生态付复杂度。”
**追问 A**：放弃运行时动态注册和细粒度租户权限。
**追问 B**：出现多租户、外部工具和不同主体授权需求后再评估。

## 13. 链接全拒取舍

**标准答案**：链接引入 canonical 目标、竞态和平台差异；P3 为可证明边界全部拒绝。
**源码位置**：`policy.py::WorkspaceGuard._reject_links/_is_link`。
**常见错误回答**：“resolve 后在 workspace 就永远安全。”检查后链接可被替换。
**面试表达**：“当前安全基线选择保守一致性，未来若放行需句柄级或平台级防竞态。”
**追问 A**：使用链接组织源码的仓库可用性下降。
**追问 B**：不能完全消除 TOCTOU，只是通过同入口复核缩小窗口。

## 14. 资源截断语义

**标准答案**：list/read/search 的已知上限可返回有用前缀并明确 truncated，因此仍成功；模型不应把截断误判为完整。
**源码位置**：`contracts.py::ToolLimits`；`tools/readonly.py`。
**常见错误回答**：“截断必然是异常”或“静默截断不标记”。
**面试表达**：“能安全给部分证据就成功加标志，无法保持语义才失败。”
**追问 A**：无法安全给部分结果时用 resource_limit_exceeded。
**追问 B**：上限是系统安全预算，模型只能选择更小范围。

## 15. 为什么没有 interrupt

**标准答案**：P3 只有 read_only；需要批准的 effect 已拒绝，没有合法 pending 动作。interrupt/checkpoint 属于 P4。
**源码位置**：`policy.py::ToolSafetyPolicy.evaluate`；安全文档第 13–14 节。
**常见错误回答**：“所有工具都先询问用户最安全。”会造成审批疲劳且混淆非法参数。
**面试表达**：“先建立静态执行门，再为具体副作用对象引入可恢复批准。”
**追问 A**：P4 插在校验/静态策略后、执行前。
**追问 B**：工具名过粗；批准必须绑定 Patch hash/preimage/workspace 状态。

## 16. ADS

**标准答案**：NTFS ADS 用冒号形成同一路径名下的数据流，简单 `.env` equality 可被 `.env::$DATA` 绕过；P3 在 resolve 前拒绝冒号。
**源码位置**：`policy.py::WorkspaceGuard._lexical_parts`；`test_readonly_tools.py::test_read_file_rejects_env_ntfs_stream_alias`。
**常见错误回答**：“Path.resolve 会自动消除 ADS 风险。”它不等于安全授权。
**面试表达**：“先拒绝平台别名语法，再做 canonical containment。”
**追问 A**：还拒绝 UNC、`\\?\`、保留设备名和尾随点/空格。
**追问 B**：fixture 写秘密 marker，只断言 code 且 marker/危险路径不在结果。

## 17. 证明拒绝未执行

**标准答案**：synthetic tool 内部递增计数器，Policy 拒绝后断言为 0；Validation 还同时断言 Policy Spy 为 0。
**源码位置**：`tests/unit/test_safe_tool_executor.py`。
**常见错误回答**：“看到 error code 就说明没执行。”实现可能先执行再返回拒绝。
**面试表达**：“安全顺序要用副作用观测点证明，不只测最终文本。”
**追问 A**：Code 只证明输出，不证明调用时序。
**追问 B**：分别把 synthetic tool 标为 WRITE/COMMAND，断言 side_effect_not_supported 且 counter=0。

## 18. 异常脱敏

**标准答案**：按异常类型映射固定 Category/Code/message，未知异常统一 tool_execution_error；不使用 `str(exc)`。
**源码位置**：`executor.py::SafeToolExecutor.execute` 的 Execution except 分支。
**常见错误回答**：“把异常全文给模型更利于修复。”会泄漏密钥、路径和内部堆栈。
**面试表达**：“保留机器可恢复类别，丢弃不可信异常载荷。”
**追问 A**：异常字符串可含用户路径、token 或库内部信息。
**追问 B**：FileNotFound/Permission/类型/编码/资源有具体分类，其他归 execution_failure。

## 19. 同轮失败阻断排错

**标准答案**：检查 ToolNode 是否在循环内 early return/raise，Executor 是否把异常变 Envelope；断言输入调用数等于输出 ToolMessage 数且 ID/顺序相同。
**源码位置**：`agent/nodes.py::ToolNode.__call__`；`test_agent_nodes.py`。
**常见错误回答**：“第一个失败就应该终止。”这丢失模型同轮其他证据请求。
**面试表达**：“单调用隔离失败，批处理层始终遍历完整列表。”
**追问 A**：比较 Tool Call ID 列表、ToolMessage ID 列表和审计 ID 列表。
**追问 B**：非法结构在 Normalization 转 error ToolMessage，不向 ToolNode 抛出。

## 20. 为什么不是沙箱

**标准答案**：Policy 与 Agent 在同一宿主进程和权限下，只减少误访问；无法抵抗进程攻破、恶意依赖、内核问题或所有竞态。
**源码位置**：`docs/security/TOOL_SAFETY_POLICY.md::当前未解决的边界`。
**常见错误回答**：“拒绝绝对路径就等于文件系统沙箱。”应用层校验不是系统隔离。
**面试表达**：“这是 capability/policy guard，不是权限边界；强隔离需要低权限进程、容器或 OS sandbox。”
**追问 A**：宿主被攻破可绕开 Python 调用入口，Policy 不再可靠。
**追问 B**：pytest 需固定命令/环境/超时/进程回收与隔离；Patch 需审批、hash/preimage、原子应用和恢复复核。
