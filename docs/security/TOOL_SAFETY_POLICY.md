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

## 13. P3 到 P4 的审批边界

P3 只有只读工具，对副作用稳定拒绝。P4 仅新增一个 `propose_patch` write effect，并把裁决扩展为 `allow / require_approval / deny`。参数错误仍直接回填模型；只有合法、边界内、可完整生成 Diff 的单文件提案才创建 pending approval。

## 14. P4 已实现的接入点

执行顺序为 Dispatch → Validation → Policy → Proposal Preparation → Approval interrupt → Apply/Reject → ToolMessage。Policy 不生成 Proposal、不调用 interrupt、不写文件。Approval Node 不写文件且恢复时只重建相同 payload。Apply Node 重新验证 workspace、链接、original/proposed SHA-256、完整 Diff 与行数绑定后才原子替换。

## 15. P4 安全不变量

- 模型没有 `write_file` 或 `apply_patch` 工具；`propose_patch` 的函数体也不写文件。
- 一个含 patch 的 AIMessage 必须只有一个调用；混合/双 patch 整批返回 `approval_batch_not_supported`。
- API 只接受 proposal_id、approve/reject 和限长 comment，不接受 new_content、thread_id 或 State update。
- Diff 完整且超限即拒绝；绝不批准截断 Diff。内部 proposed content 只存 checkpoint，不单独返回 API 或审计。
- 等待期间文件变化返回 `stale_patch`；拒绝、非法决定、proposal mismatch 和重复 resume 都不会写入；同进程并发决定按 run 锁住完整“状态复核 + resume”临界区。
- 临时文件与目标同目录，写入后 flush/fsync/close，再 `os.replace`；失败尽力清理，错误不暴露临时路径或裸异常。
- 最后一个模型轮次提出 patch 时不启动审批，返回 `approval_not_started_budget_exhausted`。
- P4 的 InMemorySaver 进程重启即丢失；P6 SQLite 也不能替代恢复时的外部状态复核。

## 16. P5 固定 pytest 边界

P5 只允许 `PytestRunner` 创建测试子进程，固定为当前项目解释器的绝对路径与参数序列 `-m pytest -q --tb=short <configured-relative-target>`。它使用 `asyncio.create_subprocess_exec`，不经过 shell，不接受模型/API 的 command、args、cwd、env、executable 或任意 timeout。

测试目标先经过和文件工具相同的 workspace 相对路径规则及实际 resolve/link/containment 复核。cwd 固定为 workspace。环境采用 allowlist，只保留必要 Windows/临时目录变量并强制 UTF-8、禁 bytecode、禁 user site、禁 pytest plugin autoload；API Key、Provider Base URL、`PYTEST_ADDOPTS`、`PYTHONPATH` 和 RepoPilot 私密配置不继承。

stdout/stderr 持续读取并共享硬字节预算；timeout 或 output limit 后终止、必要时 kill 并回收直接 pytest 子进程。输出再做 ANSI/control 清理、UTF-8 replacement、已知 SecretStr/常见 token、workspace/解释器路径替换与字符上限。该脱敏只是 best effort，恶意测试仍可能编码或拆分秘密。PytestRunner 也不是操作系统沙箱：测试代码拥有 RepoPilot 进程用户的文件系统和网络权限，P5 只能约束启动入口、参数、环境、时间与输出。

## 17. P5 Apply、ToolMessage 与重试不变量

- Approval payload 明示批准后会应用当前 Patch 并自动运行固定 pytest；不暴露绝对解释器、环境或可编辑参数。
- Apply 成功只生成一次性 `applied_patch_context`，不立即回填成功 ToolMessage；Tester 实际运行一次后，以原 tool_call_id 生成该 Patch 唯一 ToolMessage。
- reject、invalid、stale 和 Apply failure 仍立即生成 error ToolMessage，且不进入 Tester。
- pytest exit code 由 Python 映射：只有 0 成功，只有 1 默认可进入代码修复循环；2–6、未知返回码、timeout、output limit、launch error 都是终态/基础设施结果。
- `repair_attempts` 只在 Tester 实际启动时增加。只有 `test_failures`、修复预算剩余且模型预算剩余时才能回模型；Router 不修改计数。
- 新 Patch 必须重新生成 proposal_id、重新 interrupt/审批、重新做 stale/hash 校验并重新测试；没有自动批准、相同测试自动重跑或失败 Patch 自动回滚。
- Reviewer 是确定性 Python 证据校验器，不是语义代码审查器，也不调用第二个模型；只有最近 Patch hash、对应 pytest pass/exit 0 和状态一致时才能报告 repaired。

## 16. 当前未解决的边界

这不是操作系统沙箱。Agent 与 FastAPI 宿主进程拥有相同系统权限；Policy 只能降低误访问和模型诱导风险，不能防御被攻破的 Python 进程、内核/文件系统竞态或恶意依赖。策略检查与执行前复核缩小 TOCTOU 窗口，但不能提供原子文件系统保证。P3 也没有文件写入、命令执行、网络隔离、审批、持久状态或跨进程恢复。

## 18. P6 本地持久化与 Trace 边界

- `.repopilot` 被工具策略拒绝，list/read/search/propose_patch 均不能访问。
- SQLite checkpoint 未加密，可能含 Goal、messages、tool calls、Patch 和测试反馈；文件权限不等于静态加密。
- RuntimeStore 不复制完整 Goal、messages、Diff、proposed content 或完整 pytest 输出。
- Trace payload 先经字段 allowlist，只持久化短标量业务元数据；密钥、Base URL、环境、异常和数据库路径禁止写入。
- ContextManager 不修改持久 State、不改 Tool Call 参数、不拆 AIMessage/ToolMessage 协议。
- running 与 awaiting approval 不能删除；终态清理跨两库为 best-effort 幂等步骤。
- 数据库错误不得静默回退 InMemorySaver；当前锁只适用于单进程。
