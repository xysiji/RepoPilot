# RepoPilot 面试题生成与模拟计划

## 1. 产物与时间点

本文件只定义题目主题和生成规范，不提前编写 P0–P7 的完整答案。每个开发阶段验收后，依据当时真实代码生成：

- `docs/interview/PX-questions.md`
- `docs/interview/answers/PX-answers.md`

每阶段固定 20 个主问题：5 个概念题、5 个源码题、5 个设计取舍题、5 个异常/安全/排错题。答案必须引用真实 RepoPilot 文件、类、函数和测试；若实现名称与本计划不同，以真实代码为准，并更新题目。

## 2. 难度分层

| 等级 | 目标 | 典型动作 | 通过标准 |
| --- | --- | --- | --- |
| L1 基础 | 判断是否理解术语 | 定义、描述主链 | 不用空泛类比，能说输入/输出 |
| L2 源码 | 判断是否真正做过 | 定位、沿调用链解释 | 2 分钟内指出真实代码和测试 |
| L3 设计 | 判断取舍能力 | 比较方案、说明成本 | 能结合第一版约束，不喊“高并发/可扩展”口号 |
| L4 故障 | 判断排错与安全意识 | 构造失败、提出证据 | 先给假设和观测点，再给修复与回归测试 |

每类 5 题按 `2 个 L1/L2 + 2 个 L2/L3 + 1 个 L3/L4` 分布。安全阶段 P3/P4/P5/P6 的异常题至少 3 个达到 L4。

## 3. 连续追问规则

每个主问题至少生成两个递进追问，不能只是换一种说法：

1. **机制追问**：要求沿真实调用链说明“输入如何变成输出”，定位到函数和 State 字段。
2. **边界追问**：改变一个条件，例如重启、并发、超时、dirty tree、模型输出非法，问原实现如何表现。
3. **取舍追问（可选第三问）**：要求比较一个可行替代方案，并指出何时会改选。

源码题必须追加“对应测试在哪里、断言了什么”；安全题必须追加“如何故障注入、如何证明没有副作用”；设计题必须追加“放弃了什么能力”。禁止生成能靠背定义回答的追问。

## 4. 源码定位要求

阶段答案必须包含：

- 可点击的真实文件路径和具体类/函数/节点名。
- 一条从 API/graph node 到 service/tool/result 的调用链。
- 至少一个正常测试和一个失败测试的名称。
- 相关 State 字段的 writer、reader 和路由条件。
- 若借鉴 KamaClaude，只能说明设计对照；核心答案以 RepoPilot 真实实现为准。

没有实现的计划路径不得写成“项目中已经做了”。P0 答案不能引用 P1 代码，P4 答案不能借 P6 SQLite 掩盖当阶段的 InMemorySaver 限制。

## 5. P0 题目主题

| # | 概念题 | 源码题 | 设计取舍题 | 异常/安全/排错题 |
| --- | --- | --- | --- | --- |
| 1 | Pydantic v2 校验与序列化边界 | 定位 Settings 创建和环境覆盖链 | 为什么配置在启动时校验 | 缺 API key 的失败位置与脱敏 |
| 2 | FastAPI lifespan 与依赖注入 | 定位 app factory/lifespan 清理 | 为什么不用全局模型单例硬编码 | lifespan 初始化一半失败如何回收 |
| 3 | LangChain ChatModel 抽象 | 定位 model factory 与 fake 注入 | Provider 抽象与直接 SDK 的取舍 | 未知 Provider/模型名如何报错 |
| 4 | secret 与普通配置的区别 | 定位对外 health schema | 为什么 secret 不进 State/checkpoint | 配置对象被日志/JSON 泄露的检测 |
| 5 | 单元测试中的 dependency inversion | 定位 fake model 测试 | 为什么 P0 不做真实 API 必需测试 | 网络波动/超时如何与配置错区分 |

## 6. P1 题目主题

| # | 概念题 | 源码题 | 设计取舍题 | 异常/安全/排错题 |
| --- | --- | --- | --- | --- |
| 1 | Tool Calling 的完整消息协议 | 定位 AIMessage.tool_calls 到 ToolMessage | 为什么先手写最小闭环再上图 | tool_call_id 不配对会怎样 |
| 2 | 工具 schema 与模型决策 | 定位 Pydantic args schema | docstring/schema 过宽的代价 | extra 参数和缺参数如何反馈 |
| 3 | ReAct 中 observation 的角色 | 定位工具结果回填与第二次模型调用 | 工具异常返回消息还是抛出 | 同一失败工具被无限调用如何终止 |
| 4 | 只读工具与副作用工具 | 定位模型实际绑定工具列表 | 为什么 P1 不提供 write/bash | 外部 symlink/absolute path 越界 |
| 5 | fake/scripted model 测试方法 | 定位脚本化响应序列 | 真实模型 E2E 为什么不是核心 CI | 模型未按预期调用工具如何诊断 |

## 7. P2 题目主题

| # | 概念题 | 源码题 | 设计取舍题 | 异常/安全/排错题 |
| --- | --- | --- | --- | --- |
| 1 | StateGraph 的 node/edge/START/END | 定位 graph builder 和入口边 | 显式图与手写 while 的取舍 | 缺 END/错误条件边如何表现 |
| 2 | State 字段与 messages 的边界 | 定位 plan/evidence writer-reader | 为什么不把 plan/test 都塞 messages | State 中不可序列化对象如何发现 |
| 3 | reducers 与单 writer 原则 | 定位 messages/evidence/errors reducer | append 与 replace 的取舍 | 并发/多节点写无 reducer 冲突 |
| 4 | 结构化 ExecutionPlan | 定位 planner structured output | TaskManager 文件 CRUD vs State plan | 非法 plan 的修复次数和停止条件 |
| 5 | 条件路由与循环预算 | 定位 executor router/max rounds | 预算放配置还是 State | off-by-one 造成多调用或早停 |

## 8. P3 题目主题

| # | 概念题 | 源码题 | 设计取舍题 | 异常/安全/排错题 |
| --- | --- | --- | --- | --- |
| 1 | canonical path 与 containment | 定位 WorkspaceGuard 主校验链 | 统一 guard vs 每工具各校验 | `..`、绝对路径、UNC/盘符绕过 |
| 2 | symlink/junction 与 TOCTOU | 定位 resolve/symlink 检查 | 全拒 symlink vs 允许工作区内 symlink | 审批/读取间替换链接的故障注入 |
| 3 | subprocess exec 与 shell 注入 | 定位 run_tests 参数数组 | 固定 pytest vs 通用 Bash | 恶意 target/参数能否注入命令 |
| 4 | timeout/terminate/kill/reap | 定位子进程超时清理 | timeout 是否值得自动重试 | Windows/Unix 子进程树和孤儿进程 |
| 5 | 结果截断与结构化 ToolError | 定位 max bytes/matches 和 error code | 保留头/尾/完整输出的取舍 | 大输出、二进制、decode 失败排错 |

## 9. P4 题目主题

| # | 概念题 | 源码题 | 设计取舍题 | 异常/安全/排错题 |
| --- | --- | --- | --- | --- |
| 1 | LangGraph interrupt/resume | 定位 approval node 与 API resume | interrupt vs 内存 Future | resume 时节点重放导致重复副作用 |
| 2 | Patch hash 与授权绑定 | 定位 hash 生成和批准复核 | 批准工具名 vs 批准具体 Patch | 旧 hash、其他 thread、重复批准 |
| 3 | preimage hash/乐观并发 | 定位 apply 前 workspace 五项复核 | 锁工作区 vs hash 检查 | 审批等待时人工改文件/HEAD |
| 4 | unified diff 与 FileEdit | 定位 generate_patch 唯一匹配逻辑 | 模型直接生成 diff vs 结构化 edit | old snippet 重复/缺失/空 Patch |
| 5 | 多文件写与回滚 | 定位预检、临时文件、os.replace | 真事务 vs best-effort rollback | 第二个文件写失败后的仓库状态 |

## 10. P5 题目主题

| # | 概念题 | 源码题 | 设计取舍题 | 异常/安全/排错题 |
| --- | --- | --- | --- | --- |
| 1 | pytest 作为反馈 oracle | 定位 tester node/TestResult | 跑全量 vs focused tests | 测试本身错误如何误导 Agent |
| 2 | retry、replan 与 infrastructure retry | 定位 test_router 三路分支 | 为什么代码失败回 planner | assertion/collection/timeout 分类错误 |
| 3 | max_retries 与终止证明 | 定位 retry_count writer/reader | 默认 0/1/2 的成本取舍 | off-by-one 导致多写一次 |
| 4 | 增量 Patch 与累计 Diff | 定位每轮 Patch hash 和 reviewer | 每轮回滚 vs 累积修复 | 第二轮批准拒绝后的最终状态 |
| 5 | deterministic final report | 定位 ReviewResult/FinalReport 生成 | 首版为何不用 LLM reviewer | Diff 越界、冲突标记、测试失败漏报 |

## 11. P6 题目主题

| # | 概念题 | 源码题 | 设计取舍题 | 异常/安全/排错题 |
| --- | --- | --- | --- | --- |
| 1 | checkpoint/thread/session 区别 | 定位 checkpointer compile/config | SQLite vs 自建 SessionStore | checkpoint 缺失/损坏如何恢复 |
| 2 | durable interrupt 与 thread_id | 定位重启后 approval resume | 稳定 ID 与 HTTP request ID | 同 thread 并发/重复恢复 |
| 3 | checkpoint 与外部世界一致性 | 定位恢复时 HEAD/hash 重验 | 信 checkpoint vs 重验 workspace | 重启后工作区删除/HEAD 改变 |
| 4 | trace/log/checkpoint 的区别 | 定位 TraceEvent reducer/report | 本地 trace vs LangSmith | secret/源码 payload 泄露检测 |
| 5 | context budget 与有损 compact | 定位消息/工具结果限长 | 截断、摘要、完整回放取舍 | tool call 配对被截断或摘要丢事实 |

## 12. P7 题目主题

| # | 概念题 | 源码题 | 设计取舍题 | 异常/安全/排错题 |
| --- | --- | --- | --- | --- |
| 1 | subgraph 与普通 node/tool | 定位 reviewer subgraph 组合 | 何时值得拆 subagent | 子图异常如何回退父图 |
| 2 | per-invocation/per-thread persistence | 定位 reviewer compile 配置 | reviewer 是否需要长期记忆 | checkpoint namespace 冲突 |
| 3 | 父子 State 映射与冷上下文 | 定位 ReviewInput/Result adapter | 共享 messages vs 最小投影 | 必要证据漏传/私密上下文过传 |
| 4 | reviewer 工具最小权限 | 定位只读工具装配测试 | Prompt 禁写 vs registry 无写工具 | apply_patch 被误加入 reviewer |
| 5 | LLM reviewer 与确定性 reviewer | 定位 fallback 与结果 schema | 质量收益、成本和幻觉取舍 | 输出非法/超时/虚构问题如何处理 |

## 13. 模拟面试方式

### 13.1 阶段模拟（20–25 分钟）

1. 随机抽 1 个概念题，限时 90 秒回答。
2. 抽 1 个源码题，共享屏幕在 2 分钟内定位文件/函数/测试。
3. 面试官连续给机制和边界追问，不允许先看笔记。
4. 抽 1 个故障题，候选人先说假设、观测点、复现测试，再说修复。
5. 最后用 60 秒总结该阶段“问题、方案、取舍、证据”。

### 13.2 全项目模拟（45–60 分钟）

- 3 分钟项目介绍。
- 10 分钟画出 graph、审批和测试回环。
- 10 分钟深挖 P3/P4 安全边界。
- 10 分钟深挖 P6 恢复与 Trace。
- 10 分钟现场源码定位与故障推演。
- 5 分钟反思：第一版删了什么、下一步最值得做什么。

评分维度各 20%：准确性、源码证据、边界意识、取舍能力、表达结构。只会说框架名而不能定位代码，最高不得超过 60 分；安全题不能给出验证测试，P3/P4 不通过。

## 14. 最终项目介绍提纲

最终 3 分钟介绍按以下顺序，不背诵技术栈清单：

1. **问题**：普通 LLM 会建议改代码，但缺少可控的读取、审批、测试反馈和停止条件。
2. **最小闭环**：任务与 workspace -> 结构化 plan -> 只读工具证据 -> Patch -> interrupt 审批 -> apply -> pytest -> 有限重规划 -> Diff/reports。
3. **最关键设计**：模型只推理，确定性 Python 控制 workspace、路由、批准、写入、超时和重试上限。
4. **框架取舍**：LangGraph 替代手写 loop/session，LangChain 替代 Provider/registry，FastAPI 替代 daemon/IPC；为什么没有 TUI/EventBus/Bash。
5. **安全证据**：路径/symlink、Patch hash/preimage、固定 subprocess、审批重放、dirty tree 和测试名称。
6. **可恢复与观测**：SQLite thread/checkpoint、本地 Trace、LangSmith opt-in。
7. **结果与限制**：展示真实 Demo、测试数量/关键失败案例；承认单用户、本地、无沙箱、无并行 Agent。
8. **演进方向**：先 reviewer subgraph；只有出现真实外部工具需求才接标准 MCP adapter。

## 15. 答案生成禁区

- 不得把课程或 KamaClaude README 的表述直接当作 RepoPilot 项目成果。
- 不得引用不存在的类、函数、测试数量或性能数据。
- 不得用 Prompt 规则回答代码安全问题。
- 不得把 LangGraph/LangChain 的默认行为说成自己实现的算法；必须说明框架承担什么、自有代码承担什么。
- 不得隐藏第一版删除的能力和已知限制。
