# P3 Tool Safety Policy

## 1. 威胁模型

模型输出、用户任务文本、工具参数、工作区文件名和文件内容都视为不可信输入。主要风险是路径逃逸、敏感文件读取、Windows 路径别名、链接跳转、超大输出、异常泄漏，以及把未来副作用工具误当只读工具执行。

## 2. 信任边界

LangChain 负责标准 Tool Schema 和消息协议，LangGraph 负责节点路由。安全裁决由 RepoPilot Python 代码负责。`SafeToolExecutor` 是工具进入真实执行的唯一门；`WorkspaceGuard` 是三个文件工具共享的路径入口。

## 3. 模型不可信原则

模型可以选择工具和提出参数，但不能声明 effect、覆盖 Policy、扩大系统上限或决定错误是否成功。Prompt 中的“不要读取秘密”不构成安全边界；生产工具 mapping、Pydantic 校验和 Workspace Policy 才构成可测试边界。

## 4. Schema 与 Policy 的区别

Schema 校验类型、必填字段、长度、数值范围、空字符串、NUL、后缀格式和额外字段。Policy 在 Schema 成功后判断 effect、workspace containment、敏感名称、链接和平台路径别名。非法参数不会进入 Policy，Policy 拒绝不会调用工具。

## 5. 生产工具 Effect

| 工具 | Effect | P3 结果 |
| --- | --- | --- |
| `list_files` | `read_only` | Policy 通过后执行 |
| `read_file` | `read_only` | Policy 通过后执行 |
| `search_code` | `read_only` | Policy 通过后执行 |

`write`、`command` 返回 `side_effect_not_supported`；缺少分类返回 `unclassified_tool_effect`。两者都不会执行。effect 只来自 Python mapping，工具参数模型禁止额外字段。

## 6. Workspace 规则

工具只接受相对路径。Guard 拒绝父路径组件、绝对路径、Windows 盘符、UNC、设备前缀和 resolve 后位于 workspace 外的路径。Policy 检查后，工具实际访问前再次通过同一个 Guard 解析。遍历发现的每个条目也接受同一 Guard 检查。

## 7. 敏感路径规则

大小写不敏感地拒绝 `.env*`、`.git`、`.venv`、`__pycache__`、`id_rsa*`、`id_ed25519*`、`.pem` 和 `.key`。这些规则按路径段应用，因此嵌套文件同样受保护。普通 `.py`、`.md` 和其他源文件不会因宽泛关键词被阻止。

## 8. Windows ADS、Junction 和设备路径

冒号别名（包括 `.env::$DATA`）被拒绝；`\\?\`、`\\.\`、UNC、盘符路径、`CON/NUL/AUX/PRN/COM1..9/LPT1..9` 和尾随空格/点均被拒绝。任何路径段为 Symlink 或 Junction 时返回 `link_path_denied`，即使目标最终仍在 workspace 内。

## 9. 资源上限

| 资源 | 上限/语义 |
| --- | --- |
| list 深度 | 参数最大 5 |
| list 条目 | 系统最大 200，超过后成功并 `truncated=true` |
| read 字符 | 系统最大 20,000，超过后成功并 `truncated=true` |
| search 参数结果数 | 最大 100 |
| search 单文件 | 最大 256 KiB，超出跳过 |
| search 扫描深度 | 最大 8 |
| search 单行返回 | 最大 500 字符 |
| query | 最大 200 字符 |

参数请求超过上限在 Validation 阶段失败。当前 list/read/search 可安全截断的情况统一视为成功；无法安全截断的合成/未来工具可返回 `resource_limit_exceeded`。

## 10. Fail Closed

未知工具停在 Dispatch；未知 effect、Policy 内部失败、有副作用或需要审批的工具停在 Policy；非法工具结果停在 Normalization。任何分支都生成对应 ToolMessage 和审计记录，不以“默认允许”恢复。

## 11. 错误分类

| Phase | Category 示例 | Code 示例 |
| --- | --- | --- |
| dispatch | invalid_request | `unknown_tool` |
| validation | invalid_request | `invalid_arguments` |
| policy | policy_denied | `sensitive_path_denied`、`link_path_denied`、`side_effect_not_supported` |
| execution | filesystem | `not_found`、`not_a_file`、`permission_denied` |
| execution | unsupported_content | `binary_file`、`invalid_encoding` |
| execution | resource_limit | `resource_limit_exceeded` |
| execution | execution_failure | `tool_execution_error` |
| normalization | internal_failure | `invalid_tool_result` |

Phase 说明失败发生在哪一步，Category 用于聚合，Code 用于稳定测试和恢复判断。消息只使用通用安全文本，不拼接原始异常、ValidationError input 或危险参数。

## 12. 回填模型的错误

所有工具失败都编码为固定 `success/data/error` JSON，并以相同 `tool_call_id` 的 `ToolMessage(status="error")` 回填模型。模型可以换安全路径或总结限制，但不能推翻 Policy。同轮第一个失败不阻止后续调用。

## 13. 为什么 P3 没有人工审批

P3 只有只读工具。审批生命周期、暂停恢复、批准对象绑定和写入复核属于 P4；提前加入会把未完成的副作用语义混入当前安全基线。P3 对 `requires_approval=true` 直接稳定拒绝，不创建 pending 状态，也不调用 `interrupt()`。

## 14. P4 接入点

P4 应在已校验参数和静态 Policy 之后、任何副作用执行之前加入 Approval。批准必须绑定具体 Patch/hash/preimage，而不是仅绑定工具名。P4 不能削弱 P3 WorkspaceGuard、effect classification、失败 Envelope 或 ToolMessage 配对。

## 15. 当前未解决的边界

这不是操作系统沙箱。Agent 与 FastAPI 宿主进程拥有相同系统权限；Policy 只能降低误访问和模型诱导风险，不能防御被攻破的 Python 进程、内核/文件系统竞态或恶意依赖。策略检查与执行前复核缩小 TOCTOU 窗口，但不能提供原子文件系统保证。P3 也没有文件写入、命令执行、网络隔离、审批、持久状态或跨进程恢复。
