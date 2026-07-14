# P5 面试题答案：pytest 反馈与有限修复

## 1. pytest 事实来源

- **标准答案**：输出文字只用于安全摘要，成功/失败由进程 exit code 决定。0 是通过，1 是已运行测试失败，2–6 和未知值分别进入明确的非代码修复结果。
- **源码位置**：`src/repopilot/testing/contracts.py::classify_pytest_exit_code`；`testing/pytest_runner.py::PytestRunner.run`。
- **常见错误回答**：搜索输出中是否包含 `passed` 或 `FAILED`。
- **面试表达**：“我把 exit code 当 oracle，把文本当观测数据，避免语言、插件和格式改变状态机。”
- **追问 1 答案**：0 passed，1 test_failures，2 interrupted，3 internal，4 usage，5 no tests，6 warnings exceeded。
- **追问 2 答案**：5 表示没有测试证据，不是成功；也不能可靠归因于业务代码缺陷，所以直接 Review/Report。

## 2. 两个预算

- **标准答案**：`max_steps` 限模型调用，`max_repair_attempts` 限成功 Apply 后实际启动 pytest 的次数；二者都剩余且 outcome 为 test_failures 才能继续。
- **源码位置**：`src/repopilot/agent/state.py::create_initial_state`；`agent/nodes.py::TesterNode`；`agent/routing.py::route_after_tester`。
- **常见错误回答**：把工具次数、模型次数和 pytest 次数合成一个 retry。
- **面试表达**：“一个预算控制推理成本，一个预算控制写入和验证副作用，Router 取交集。”
- **追问 1 答案**：Tester 真正调用 Runner 一次后，在同一节点更新中加一；reject/stale/apply failure 不增加。
- **追问 2 答案**：两个单调整数都有有限上限，只有严格小于上限才有回边；其他结果都去 Reviewer，因此没有无限回路。

## 3. pytest 非沙箱

- **标准答案**：pytest 导入并执行项目代码，子进程拥有 RepoPilot OS 用户权限。P5 只限制启动入口、args、cwd、env、时间和输出。
- **源码位置**：`src/repopilot/testing/pytest_runner.py::PytestRunner`；`docs/security/TOOL_SAFETY_POLICY.md` P5 固定 pytest 边界。
- **常见错误回答**：子进程天然隔离文件系统和网络。
- **面试表达**：“这是受控执行入口，不是容器或内核沙箱。”
- **追问 1 答案**：能阻断 shell/参数注入、环境继承、无限时间和无限输出；不能阻断测试代码主动访问权限范围内资源。
- **追问 2 答案**：禁 autoload 降低隐式代码加载，但可能使依赖插件的项目无法运行；当前将其分类为基础设施结果并记录限制。

## 4. ToolMessage 时点

- **标准答案**：Apply 成功不等于修复成功，所以 P5 等 Tester 结束后再生成包含 patch_applied 和 test outcome 的唯一 ToolMessage。
- **源码位置**：`src/repopilot/agent/nodes.py::_successful_apply_update`、`TesterNode`；`testing/feedback.py::build_test_feedback_message`。
- **常见错误回答**：Apply 和 Tester 各返回一个 ToolMessage。
- **面试表达**：“一个 tool call 只有一个协议结果，P5 把写入和验证事实合并后再结算。”
- **追问 1 答案**：reject、stale、invalid 或 Apply failure 立即生成 error ToolMessage，因为不会再进入 Tester。
- **追问 2 答案**：重复会破坏 Provider 的 tool-call 配对，也会让 Reviewer 无法判断哪条结果有效。

## 5. Reviewer 边界

- **标准答案**：Reviewer 只验证 pending/context、ToolMessage 唯一性、最近文件 hash、Patch/Test ID、pytest outcome/exit code 和预算证据，不判断代码语义或架构质量。
- **源码位置**：`src/repopilot/review/reviewer.py::DeterministicReviewer.review`。
- **常见错误回答**：Reviewer 再调用一个模型评价代码好坏。
- **面试表达**：“它是工作流一致性审计，不是语义 code review。”
- **追问 1 答案**：事实条件可确定计算；第二个 LLM 只会增加成本、非确定性和误报成功风险。
- **追问 2 答案**：证据完整且 pass/hash/ID 全一致为 passed；明确冲突或测试未过为 failed；pending、未消费 context 或缺结果为 incomplete。

## 6. 固定命令源码链

- **标准答案**：Runner 用当前解释器绝对路径构造 tuple，再由 `create_subprocess_exec(*command, cwd=workspace, env=allowlist)` 启动。
- **源码位置**：`src/repopilot/testing/pytest_runner.py::command`、`build_environment`、`run`。
- **常见错误回答**：拼接字符串后传给 PowerShell。
- **面试表达**：“命令能力由代码封闭，API 只有修复预算，没有执行参数。”
- **追问 1 答案**：参数序列绕过 shell 解析和引用规则，`create_subprocess_exec` 不启动 shell。
- **追问 2 答案**：不能；`AgentRunRequest` extra forbid，服务只从 Settings 注入 target/timeout/output/system limit。

## 7. Tester 合并反馈

- **标准答案**：Apply context 带 proposal/tool-call ID、相对路径、双 hash、Diff 计数和审批事实；Tester 加入 result 后用原 ID 构造 ToolMessage。
- **源码位置**：`src/repopilot/testing/contracts.py::AppliedPatchContext`；`agent/nodes.py::TesterNode`。
- **常见错误回答**：把完整 proposed content 或 workspace 绝对路径交给 Tester/消息。
- **面试表达**：“Apply 和 Test 之间传最小证据，不传能力对象和敏感内容。”
- **追问 1 答案**：允许安全元数据；禁止完整 old/new content、临时路径、绝对 workspace、Runner、env 和 subprocess。
- **追问 2 答案**：Tester 更新完成时把 `applied_patch_context` 置空；没有 context 的再次执行不会启动 Runner。

## 8. route_after_tester

- **标准答案**：只有 latest result 存在、outcome 为 test_failures、status running、attempts 小于上限且 model_calls 小于上限时返回 model；其他都返回 reviewer。
- **源码位置**：`src/repopilot/agent/routing.py::route_after_tester`。
- **常见错误回答**：任何非零 exit code 都回模型。
- **面试表达**：“回边是枚举和两个预算的纯函数，不读输出文本。”
- **追问 1 答案**：Router 必须纯只读；计数由实际执行副作用的 Tester 写，避免路由重算或重复增加。
- **追问 2 答案**：都去 reviewer，再由 FinalReport 映射为 timeout 或 test_infrastructure_error。

## 9. 持续读取与输出上限

- **标准答案**：两个异步 drain task 持续读 stdout/stderr，共享 `_OutputBudget`；达到上限发事件并终止进程，之后清理、解码、脱敏和最终截断。
- **源码位置**：`src/repopilot/testing/pytest_runner.py::_OutputBudget`、`_drain`、`_stop_process`；`testing/feedback.py::sanitize_test_output`。
- **常见错误回答**：`communicate()` 全量读完后再截断。
- **面试表达**：“限制发生在采集阶段，不只是展示阶段。”
- **追问 1 答案**：独立预算可让 stdout+stderr 合计保存两倍上限；共享预算才是进程级硬上限。
- **追问 2 答案**：UTF-8 使用 replacement，ANSI regex 删除，除换行/制表外的危险控制字符过滤。

## 10. Final Report 失败措辞

- **标准答案**：Builder 先按 Review、state status、last patch error 和 TestOutcome 选择枚举 outcome，再由固定映射产生 summary，失败分支没有成功模板。
- **源码位置**：`src/repopilot/review/report.py::_select_outcome`、`_summary`。
- **常见错误回答**：让模型自由总结并相信它说“已修复”。
- **面试表达**：“结构化 outcome 先于自然语言摘要，摘要只是枚举投影。”
- **追问 1 答案**：保存限长 `model_final_text`，同时 outcome 为 repair_abandoned。
- **追问 2 答案**：它们可能含源码、系统提示、密钥、绝对路径和无限输出，也不是客户端需要的稳定契约。

## 11. 不开放通用测试工具

- **标准答案**：通用 args/shell 会把任意命令执行重新暴露给模型，破坏 P3 effect policy 和 P4 人工审批边界。
- **源码位置**：`src/repopilot/testing/pytest_runner.py`；`schemas/agent.py::AgentRunRequest`；`tools/policy.py::PRODUCTION_TOOL_EFFECTS`。
- **常见错误回答**：只要 Prompt 写“不要执行危险命令”即可。
- **面试表达**：“模型没有测试命令能力，只有 Patch 提案能力。”
- **追问 1 答案**：pytest 本身仍执行任意项目代码，可能访问文件和网络。
- **追问 2 答案**：测试节点 ID 必须来自可信解析结果、经过服务端格式/路径 allowlist，并在最终阶段强制全量测试。

## 12. 不自动回滚

- **标准答案**：失败 Patch 是下一轮当前状态和证据；回滚会改变 preimage、增加事务语义并可能丢失有用的部分修复。
- **源码位置**：`src/repopilot/agent/nodes.py::TesterNode`；`patching/applicator.py`；P5 学习笔记第 16 节。
- **常见错误回答**：测试失败一定恢复首次文件。
- **面试表达**：“P5 做递增修复，不假装提供多轮事务。”
- **追问 1 答案**：第二份 Proposal 读取第一份已写入内容作为 original/preimage。
- **追问 2 答案**：报告 approval_rejected，并列出已经应用的相对文件和此前测试失败事实，不能声称恢复原状。

## 13. 只有 exit 1 可恢复

- **标准答案**：exit 1 表示测试已收集并运行且有失败，最可靠地指向代码/断言；其他结果可能是配置、插件、收集或运行环境问题。
- **源码位置**：`src/repopilot/testing/contracts.py`；`agent/routing.py::route_after_tester`。
- **常见错误回答**：所有 nonzero 都让模型继续改。
- **面试表达**：“先做保守归因，避免环境错误驱动源码震荡。”
- **追问 1 答案**：pytest 通常用 exit 2 表示收集/import 中断，它不保证测试主体运行。
- **追问 2 答案**：未来可通过显式、可配置策略扩展，但要单独定义证据、上限和测试；P5 默认保持保守终态。

## 14. 每份 Patch 重审批

- **标准答案**：授权对象是具体 proposal，不是工具名或任务目标；新内容、Diff、hash 和 preimage 变化就必须重新授权。
- **源码位置**：`src/repopilot/patching/proposal.py::PatchProposal`；`approval/contracts.py`；`agent/nodes.py::ApprovalNode`。
- **常见错误回答**：第一次 approve 后本 run 全部 Patch 自动允许。
- **面试表达**：“一次批准绑定一次不可变提案。”
- **追问 1 答案**：proposal ID、tool-call ID、相对路径、完整 Diff、original/proposed hash、计数与理由。
- **追问 2 答案**：破坏最小授权和人类知情同意，使模型可在失败反馈后无监督写任意后续内容。

## 15. P5 不加入 SQLite

- **标准答案**：阶段范围只验证测试闭环；持久化会引入 schema 迁移、重启并发和外部状态恢复，属于 P6。
- **源码位置**：`src/repopilot/services/agent_service.py` 使用 `InMemorySaver`；`docs/development/DEVELOPMENT_PLAN.md` P6。
- **常见错误回答**：InMemorySaver 已能跨重启恢复。
- **面试表达**：“先证明状态机语义，再升级持久介质。”
- **追问 1 答案**：新进程没有旧 checkpoint，旧 run 返回 run_not_found。
- **追问 2 答案**：checkpoint 只保存历史事实，文件可能在停机期间变化；Apply/Review 仍必须重验路径和 hash。

## 16. 卡死与持续输出

- **标准答案**：并发 drain 管道，同时等待 process、limit event 和总 timeout；触发限制后 terminate，超时再 kill，最后 await 回收。
- **源码位置**：`src/repopilot/testing/pytest_runner.py::run`、`_drain`、`_stop_process`。
- **常见错误回答**：timeout 后直接返回，不等待进程。
- **面试表达**：“时间和输出都能触发同一受控停止路径。”
- **追问 1 答案**：OS pipe buffer 填满后子进程阻塞写，父进程只 wait 可能死锁。
- **追问 2 答案**：P5 best-effort 终止直接 pytest 进程，不承诺清理所有跨平台后代进程树。

## 17. 输出泄漏排查

- **标准答案**：先验证 child env 是否含敏感键，再验证 sanitizer 的精确 secret、token pattern、workspace/interpreter replacement 和最终长度；API 只取更短摘要。
- **源码位置**：`src/repopilot/testing/pytest_runner.py::build_environment`；`testing/feedback.py::sanitize_test_output`；`review/report.py`。
- **常见错误回答**：只在 API 最后做字符串替换。
- **面试表达**：“源头少带秘密，出口再做 best-effort 遮罩，双层防线。”
- **追问 1 答案**：allowlist 减少测试进程可见的父环境秘密；redaction 处理测试自己打印的已知值和路径。
- **追问 2 答案**：恶意代码可以拆分、编码、哈希或网络外传，模式匹配不可能证明发现所有秘密。

## 18. 测试失败却 repaired

- **标准答案**：检查 latest outcome/exit、proposal ID、repair attempts、ReviewStatus/findings、final outcome，再查 ModelNode 是否把失败后的文本误标 success。
- **源码位置**：`src/repopilot/agent/nodes.py::ModelNode`、`TesterNode`；`review/reviewer.py`；`review/report.py`。
- **常见错误回答**：只改最终 summary 文案。
- **面试表达**：“先查结构化事实链，不从显示文字倒推。”
- **追问 1 答案**：latest_test_result、applied_patch_context、applied_patches、review_result、status、model_final_text。
- **追问 2 答案**：模型在 exit 1 后返回普通文字必须得到 repair_abandoned；Reviewer 只有 pass+exit0+hash/ID 一致才 passed。

## 19. 重复 approve 重跑

- **标准答案**：Service 用 run 级 asyncio lock 串行化 snapshot 检查和 resume；完成后 `snapshot.next` 为空，重复决定返回 run_already_completed。
- **源码位置**：`src/repopilot/services/agent_service.py::resume_run`、`_resume_run_locked`。
- **常见错误回答**：只依赖客户端禁用按钮。
- **面试表达**：“幂等防线位于服务端状态游标，不信 UI。”
- **追问 1 答案**：锁防同进程竞态；checkpoint next 证明图是否仍停在可恢复节点。
- **追问 2 答案**：不能严格保证。进程在外部副作用后、checkpoint 前崩溃是 P6 durable idempotency 要解决的问题，P5 只保证正常同进程路径。

## 20. no-tests、internal 与未知码

- **标准答案**：5 映射 no_tests，3 映射 internal，未知正/负数映射 unknown_exit_code；均不回模型，进入 Reviewer 和 test_infrastructure_error 报告。
- **源码位置**：`src/repopilot/testing/contracts.py::classify_pytest_exit_code`；`testing/feedback.py::test_error_code`；`review/report.py`。
- **常见错误回答**：nonzero 一律 tests_failed，或者没有测试算通过。
- **面试表达**：“我保留 pytest 的失败语义，不把所有红灯都归因于业务代码。”
- **追问 1 答案**：统一回模型会让环境或 runner 故障触发无意义的连续写入。
- **追问 2 答案**：报告提供稳定 outcome/error code、exit code、限长脱敏 summary 和 Review findings，不返回原始无限输出或内部路径。
