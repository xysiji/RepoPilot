# P5 面试题：pytest 反馈与有限修复

## 一、概念题（1–5）

### 1. 为什么 RepoPilot 把 pytest exit code 而不是输出文本作为测试事实来源？

- 追问 1：exit code 0–6 分别表示什么？
- 追问 2：为什么 exit code 5 不能算成功，也不应默认让模型修代码？

### 2. `max_steps` 与 `max_repair_attempts` 有什么区别？

- 追问 1：Tester 在什么时点增加 repair_attempts？
- 追问 2：怎样证明修复循环一定终止？

### 3. 为什么 PytestRunner 不是沙箱？

- 追问 1：P5 实际能限制哪些风险？
- 追问 2：若项目测试需要第三方 pytest 插件，禁用 autoload 有什么取舍？

### 4. P5 为什么改变 P4 Apply 成功 ToolMessage 的生成时点？

- 追问 1：reject 或 stale patch 何时生成 ToolMessage？
- 追问 2：为什么一个 propose_patch tool call 最终只能有一个 ToolMessage？

### 5. P5 的 Reviewer 审查什么，又不审查什么？

- 追问 1：为什么不用第二个 LLM？
- 追问 2：`passed`、`failed`、`incomplete` 如何区分？

## 二、源码题（6–10）

### 6. 请沿源码说明固定 pytest 命令如何构造并启动。

- 追问 1：为什么使用参数序列和 `create_subprocess_exec`？
- 追问 2：API 能否覆盖 target、args、cwd、env 或 executable？

### 7. Tester Node 如何把 Apply 和 Test 合并为原工具调用的反馈？

- 追问 1：`applied_patch_context` 保存和禁止保存哪些字段？
- 追问 2：context 如何避免同一 Patch 重复生成 ToolMessage？

### 8. `route_after_tester` 的完整条件是什么？

- 追问 1：为什么 Router 不增加计数？
- 追问 2：timeout、unknown exit code 和 no-tests 会去哪里？

### 9. Runner 如何持续读取、限制并清理 stdout/stderr？

- 追问 1：为什么 stdout/stderr 必须共享预算？
- 追问 2：非法 UTF-8、ANSI 和控制字符如何处理？

### 10. FinalReportBuilder 如何保证失败不会使用成功措辞？

- 追问 1：模型主动放弃时保留什么字段？
- 追问 2：API 为什么不返回 messages 和完整测试输出？

## 三、设计取舍题（11–15）

### 11. 为什么 P5 不提供通用 `run_tests(args)` 或 shell 工具？

- 追问 1：固定参数仍有哪些代码执行风险？
- 追问 2：未来 focused tests 应怎样设计 allowlist？

### 12. 为什么测试失败的 Patch 不自动回滚？

- 追问 1：第二份 Patch 的 preimage 是什么？
- 追问 2：若用户拒绝第二份 Patch，最终报告应如何描述当前工作树？

### 13. 为什么只有 exit code 1 默认进入修复循环？

- 追问 1：collection/import error 为什么不是同一类？
- 追问 2：某项目确实能通过改代码修复 import error 时怎么办？

### 14. 为什么每份新 Patch 都必须重新人工审批？

- 追问 1：第一次批准具体绑定哪些事实？
- 追问 2：自动批准第二份 Patch 会破坏哪个安全属性？

### 15. 为什么 P5 继续使用 InMemorySaver，而不顺手加入 SQLite？

- 追问 1：进程重启后 pending run 会怎样？
- 追问 2：P6 使用持久 checkpointer 后为什么仍要重验文件 hash？

## 四、安全与排错题（16–20）

### 16. 测试卡死且不断输出时，Runner 应如何终止并回收？

- 追问 1：只 `wait()` 且不 drain 管道会有什么问题？
- 追问 2：P5 对后代进程树有哪些已知限制？

### 17. 如何排查测试输出泄漏 API Key 或绝对路径？

- 追问 1：环境 allowlist 与输出 redaction 各解决哪一层？
- 追问 2：为什么仍必须称脱敏为 best effort？

### 18. 出现“测试仍失败但 API 返回 repaired”时，你从哪里开始定位？

- 追问 1：应检查哪些 State/Review 字段？
- 追问 2：哪些回归测试能防止模型文本覆盖测试事实？

### 19. 重复提交同一 approve 导致 pytest 跑了两次，应如何诊断？

- 追问 1：Service 的 run 级锁和 checkpoint next 分别做什么？
- 追问 2：崩溃发生在测试进程启动后、checkpoint 提交前时，P5 能否保证 exactly-once？

### 20. pytest 返回 5、3 或未知负数时系统应如何表现？

- 追问 1：为什么不能统一当 assertion failure 回模型？
- 追问 2：Final Report 如何在不泄漏原始输出的前提下帮助排错？
