# P5：pytest 反馈、有限修复与确定性报告

## 1. P5 解决的问题

P4 只能证明“某份 Patch 经人工批准并写入”，不能证明修改正确。P5 把成功 Apply 与固定 pytest、有限修复、证据审查和最终报告串成闭环，并明确区分代码失败与测试基础设施异常。

## 2. Apply 与 Test 的严格顺序

```text
propose_patch → interrupt → approve → ApplyPatch → Tester → Reviewer → FinalReport
```

只有批准且 Apply 成功的 Patch 才有 `applied_patch_context`，只有它能进入 Tester。reject、stale、invalid approval 和 Apply failure 都直接生成 error ToolMessage，不运行 pytest。

## 3. 为什么测试命令不能由模型生成

模型输出是不可信输入。若允许它给 command/args/env/cwd，就等于重新开放 P3/P4 明确删除的通用命令执行能力。P5 的模型只决定读什么和提出什么 Patch；Python 决定测试入口、参数、环境、超时、输出上限、exit code 分类和是否重试。

## 4. 固定 pytest 命令

真实参数序列为：

```text
[sys.executable, "-m", "pytest", "-q", "--tb=short", pytest_target]
```

安全显示为：

```text
<python> -m pytest -q --tb=short tests
```

默认目标是 `tests`，只接受服务端配置的安全 workspace 相对路径。

## 5. `shell=False`

实现使用 `asyncio.create_subprocess_exec(*args)`，该 API 直接传程序和参数，不解析 shell 字符串。代码中不存在 `create_subprocess_shell`、`shell=True` 或 `os.system`。参数数组减少命令注入面，但不能把 pytest 变成沙箱。

## 6. 子进程环境隔离

Runner 只从父进程保留 Windows 系统根、系统盘和临时目录等 allowlist 项，并设置：

```text
PYTHONUTF8=1
PYTHONIOENCODING=utf-8
PYTHONDONTWRITEBYTECODE=1
PYTHONNOUSERSITE=1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
NO_COLOR=1
```

它不继承 API Key、Provider Base URL、`PYTEST_ADDOPTS`、`PYTHONPATH` 或任意客户端环境。禁用自动插件可能降低某些项目兼容性，这是安全优先的已知取舍。

## 7. pytest exit code 0–6

| Exit code | TestOutcome | 是否进入代码修复循环 |
| --- | --- | --- |
| 0 | `passed` | 否，进入 Reviewer |
| 1 | `test_failures` | 仅两个预算均剩余时进入模型 |
| 2 | `interrupted` | 否 |
| 3 | `pytest_internal_error` | 否 |
| 4 | `pytest_usage_error` | 否 |
| 5 | `no_tests_collected` | 否 |
| 6 | `warnings_exceeded` | 否 |
| 其他/负数 | `unknown_exit_code` | 否 |

另有 `timeout`、`output_limit_exceeded` 和 `launch_error`，都不自动让模型继续改代码。

## 8. 为什么 exit code 是事实来源

pytest 输出文字可能换语言、版本、插件或格式，也可能包含“passed”这个词但最终退出失败。Python 只按进程 exit code 裁决；解析通过/失败数量只能作为可选展示，不参与成功判断。

## 9. Timeout

Runner 同时等待进程结束和输出上限事件，并设置总超时。超时后先 `terminate()`，短等待仍未退出再 `kill()`，最后等待直接 pytest 子进程结束。P5 不承诺跨平台清理 pytest 创建的整个后代进程树。

## 10. 输出限制

stdout/stderr 由两个异步读取任务持续 drain，共享一个硬字节预算。达到上限立即标记 `output_limit_exceeded` 并停止进程，不会先把无限输出全部存入内存。最终字符摘要还有第二道上限。

## 11. 输出脱敏

顺序为 ANSI 清理、控制字符清理、UTF-8 replacement、已知 SecretStr 精确替换、常见 token/key 模式遮罩、workspace/解释器绝对路径替换、最终截断。API 报告只取比模型反馈更短的摘要。该规则是 best effort，恶意测试仍可能通过编码、拆分或外传泄漏信息。

## 12. TestOutcome

`TestOutcome` 是 Python `StrEnum`，把 pytest、超时、输出和启动结果固定为可测试分支。Router 比较枚举，不让 LLM 根据自然语言决定可恢复性。

## 13. TestRunRecord

记录 attempt、proposal ID、outcome、exit code、时长、timeout/truncated、限长安全摘要、固定 command display、相对工作目录和时间戳。它不保存 shell 字符串、绝对 cwd、环境映射或原始无限输出。

## 14. Tester Node

Tester 防御性解析一次性 apply context，调用 Runner 一次，增加 `repair_attempts`，追加 JSON-safe TestRunRecord，更新 latest result，生成原 tool-call ID 的 ToolMessage，并消费 context。它不调用模型、不提出 Patch、不自动重跑 pytest。

## 15. ToolMessage 生成时点变化

P4 的 Apply 成功会立即生成 success ToolMessage。P5 改为：Apply 成功暂不回填，Tester 把“已应用 + 测试 outcome”合并为该 tool call 的唯一结果。这样模型看到的 propose_patch 结果不会错误暗示“写入即修复成功”。

## 16. 为什么失败 Patch 不自动回滚

第二轮 Patch 必须基于第一轮已修改的当前文件；自动回滚会改变 preimage、掩盖调试证据并引入事务策略。P5 保留失败修改，最终报告明确 tests failed；用户可通过下一份批准 Patch 继续修复。

## 17. 失败反馈如何进入模型

Tester 对 exit 1 构造 error ToolMessage，其中只有 patch_applied、outcome、exit code、attempt、duration 和限长安全摘要。LangGraph 的 messages reducer 把它追加到原 AIMessage 后；下一次模型调用收到配对消息并决定读文件、提出新 Patch 或放弃。

## 18. 为什么每份新 Patch 仍需审批

第一次批准只授权具体 proposal ID、完整 Diff、路径和双 hash，不授权“之后模型认为必要的所有修改”。第二份 Patch 的内容和 preimage 已变化，因此必须新 Proposal、新 interrupt、新批准和新 stale 校验。

## 19. repair_attempts

它表示实际成功 Apply 后启动 pytest 的次数。Proposal、reject、stale、Apply failure 都不增加；Tester 真正开始一次才加一。默认系统上限 3，配置允许 1–5，API 只能请求不高于系统配置的值。

## 20. max_steps 与 max_repair_attempts

`max_steps` 限模型调用轮次；`max_repair_attempts` 限 Apply+pytest 次数。只有：

```text
latest outcome == test_failures
and repair_attempts < max_repair_attempts
and model_calls < max_steps
```

才能 `tester → model`。两种预算互不替代。

## 21. 可修复错误与基础设施错误

exit 1 可靠表示测试已经收集运行且有失败，默认可让模型修代码。collection/import、usage、internal、no-tests、timeout、output limit、launch 和 unknown code 不可靠地说明业务代码缺陷，因此直接进入 Reviewer/Report。

## 22. Reviewer

Reviewer 检查 pending approval、未消费 context、重复 ToolMessage、最近文件 hash、最近 Patch/Test ID、outcome、exit code 和预算。它只审查工作流证据，不分析代码风格、架构质量或需求语义。

## 23. 为什么 Reviewer 不用 LLM

这些条件都有确定事实和布尔判断。用 LLM 会增加成本、非确定性和“测试失败却被文字说成成功”的风险。P7 才可能选做只读 Reviewer Subgraph，P5 不提前实现。

## 24. Final Report

`FinalReportBuilder` 从 State 生成 outcome、summary、相对 modified files、两个预算、模型/审批/Apply 计数、最新测试、Review 和错误。它不包含 messages、完整测试输出、文件内容、proposed content、密钥、Base URL、checkpoint、env 或绝对 workspace。

## 25. Graph 拓扑变化

```text
model → tools → approval → apply_patch → tester
tester → model      # 仅 exit 1 且两个预算剩余
tester → reviewer   # pass、infra、exhausted
reviewer → final_report → END
```

direct normal answer 进入 final_report；模型/状态错误先经 reviewer。节点没有同时混用固定边与条件边。

## 26. 模型主动放弃

最新测试仍为 `test_failures` 时，模型若只返回文本而不再发工具调用，ModelNode 保存 `model_final_text` 并设置 `repair_abandoned`。Reviewer/Report 不会因模型措辞把它标成 repaired。

## 27. pytest 为什么不是沙箱

pytest 会导入并执行项目代码，子进程仍拥有当前 OS 用户权限，可能读写其他可访问路径或联网。P5 只约束启动命令、参数、cwd、环境、时间和输出，无法用标准库完全隔离恶意代码。

## 28. P5 的安全限制

环境和输出脱敏均为 best effort；禁插件可能影响兼容性；terminate/kill 只保证直接进程；测试可能产生 workspace 缓存；InMemorySaver 不支持重启恢复。这些限制必须公开，不能写成“安全沙箱”。

## 29. 与 KamaClaude 测试反馈设计的差异

定向参考只提供错误分类、有限重试和输出治理问题。RepoPilot 没有迁移任意 Bash、daemon、EventBus、Task CRUD 或工具自动重试，而是独立实现 Patch 审批后固定 pytest、原 ToolMessage 回填、两个预算和确定性 Reviewer。

## 30. P6 如何升级持久化

P6 才把 InMemorySaver 换为 SQLite，并处理重启恢复、状态版本、外部 hash 重验、上下文裁剪和 Trace。持久 checkpoint 也不能证明文件系统仍与保存时一致。

## 31. 新增文件职责

- `testing/contracts.py`：TestOutcome、结果、记录、apply context 和 Runner 协议。
- `testing/pytest_runner.py`：唯一固定 pytest 子进程入口。
- `testing/feedback.py`：输出脱敏和 Patch+Test ToolMessage。
- `review/contracts.py`：Review 与 Final Report Schema。
- `review/reviewer.py`：确定性证据审查。
- `review/report.py`：安全终态映射和摘要。
- `scripts/demo_p5.py`：真实 pytest 两轮离线演示与一次耗尽场景。

## 32. 两轮修复 State 快照

第一次测试失败后：

```json
{
  "model_calls": 1,
  "repair_attempts": 1,
  "max_repair_attempts": 2,
  "latest_test_result": {"outcome": "test_failures", "exit_code": 1},
  "applied_patch_context": null,
  "status": "running",
  "pending_approval": null
}
```

模型提出第二份 Patch 后：

```json
{
  "model_calls": 2,
  "repair_attempts": 1,
  "status": "awaiting_approval",
  "pending_approval": {"proposal_id": "new-id", "tool_call_id": "patch-two"}
}
```

第二次通过并报告后：

```json
{
  "model_calls": 2,
  "repair_attempts": 2,
  "latest_test_result": {"outcome": "passed", "exit_code": 0},
  "review_result": {"status": "passed", "verified_patch_hash": true},
  "final_report": {"outcome": "repaired", "patches_applied": 2},
  "status": "repaired"
}
```

## 33. 必须亲手复写、主动修改与故障注入

建议总复写量 300 行，控制在要求的 250–380 行：PytestRunner 100 行、exit code 分类 20 行、Tester Node 55 行、`route_after_tester` 25 行、Reviewer 55 行、FinalReportBuilder 45 行。复写时禁止复制后直接运行，先写行为表和失败测试。

主动修改练习：设计“失败后只跑上次失败节点”的实验分支。节点 ID 必须来自 pytest 的可信结构化产物并经过服务端 allowlist；不能把模型文本拼进参数；首次和最终仍运行全量测试，并补路径/参数注入、空 allowlist、节点消失和最终全量失败测试。该练习不默认进入生产。

故障注入清单：改成 shell；开放 API pytest args；继承 `PYTEST_ADDOPTS`；把 exit 5 当通过；按输出文字判成功；timeout 后不 kill；保存无限输出；失败后自动批准第二份 Patch；不增加或不限制 attempts；Apply 后未测试却报 repaired；模型放弃仍报成功；Reviewer 调第二模型；reject 后运行测试；重复 resume 重跑 pytest。每项都应先观察对应测试失败，再恢复代码。

## 34. 一分钟面试口述稿

“RepoPilot P5 不把 pytest 做成模型工具。用户批准具体 Patch 后，Apply 节点原子写入，但暂不返回成功 ToolMessage；Tester 用当前项目 Python 的绝对路径和固定参数数组启动 pytest，cwd 与目标由服务端配置，环境采用 allowlist，并对时间、stdout/stderr 和脱敏摘要设硬上限。Python 按 pytest 官方 exit code 分类，只有 0 成功，只有 1 在模型轮次和修复次数都剩余时回模型。失败反馈用原 tool-call ID 回填，模型提出的新 Patch 必须重新审批。确定性 Reviewer 再核对 pending/context、ToolMessage、文件 hash、Patch/Test ID、exit 0 和预算，Final Report 才能给出 repaired。它不是沙箱，当前还用 InMemorySaver；持久恢复和上下文治理留给 P6。”

## 35. P6 后续演进补充

P5 的两预算修复闭环已在 P6 支持跨重启：第一次 pytest exit 1 后形成第二份 pending proposal，关闭应用并重新创建 Graph/Service 后仍可审批、测试并报告 repaired。完整 State 留在 checkpoint，模型只接收 ContextManager 的瞬时有界视图。
