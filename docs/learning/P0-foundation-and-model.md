# P0：项目基础、配置、模型工厂与 Health API

## 1. P0 解决的问题

P0 建立一个可验证、可替换但还不会执行 Agent 工作的应用外壳：固定 Python 3.12，使用 `uv.lock` 锁定依赖；把环境变量转换成经过 Pydantic 校验的配置；把具体模型 Provider 隔离在工厂内；允许测试注入 LangChain Fake Model；最后通过 FastAPI Application Factory 暴露不访问模型网络的 `/health`。

本阶段的验收重点不是“模型能回答问题”，而是应用是否能在没有真实密钥和网络的测试环境中稳定装配，并且错误配置能否尽早、明确地失败。

## 2. FastAPI 请求到 Health 响应的完整链路

1. 调用 `repopilot.api.app.create_app(settings, model_override)` 创建独立应用实例。
2. 工厂把已经校验的 `AppSettings` 和可选 Fake Model 放入 `app.state`，再注册 Health Router。
3. `GET /health` 到达 `repopilot.api.routes.health.health`。
4. FastAPI 通过 `get_settings` 和 `get_model_override` 从当前应用实例取出依赖。
5. `is_model_configured` 只判断 Fake Model 是否存在，或真实 Provider 的必要配置是否完整；它不创建客户端、不发送请求。
6. 路由构造 `HealthResponse`，FastAPI 再按响应模型序列化并校验公开字段。

链路中的边界很清楚：Application Factory 负责装配，Dependency 负责取值，Route 负责 HTTP 语义，Schema 负责公开契约，模型工厂只负责模型构造。

## 3. 配置加载链路

`load_settings()` 只在 `create_app()` 被调用且调用方没有传入配置时执行。它将可选 `.env` 路径传给 `AppSettings`；Pydantic Settings 按构造参数、环境变量、dotenv 和字段默认值处理配置，再执行字段校验器。

关键约束如下：

- 环境变量统一使用 `REPOPILOT_` 前缀。
- `model_temperature` 只允许 0 到 2，`model_timeout_seconds` 必须大于 0 且不超过 300。
- 空字符串 API Key 被转换为缺失值，真正创建 OpenAI 模型时给出明确异常。
- `model_api_key` 使用 `SecretStr`，`safe_dump()` 进一步排除 API Key 和可能含敏感查询参数的 Base URL。
- 模块导入不会读取 `.env`，也没有全局 Settings 单例。
- 测试使用 `AppSettings(..., _env_file=None)`，不依赖开发机 `.env`。

## 4. 模型工厂创建链路

`create_chat_model(settings, model_override)` 按固定顺序工作：

1. 若传入 `model_override`，直接返回该 `BaseChatModel`。
2. 否则检查 `model_provider` 是否为当前唯一支持的 `openai`。
3. 检查 OpenAI 所需的 `model_api_key`，缺失时抛出 `MissingModelConfigurationError`。
4. 把已校验的模型名、密钥、Base URL、temperature 和 timeout 传给 `ChatOpenAI`。
5. 返回统一的 `BaseChatModel`，不调用 `invoke()`，因此构造阶段不应发送模型请求。

当前没有 Provider Registry。只有第二个真实 Provider 被确认后，才有理由引入映射或插件机制。

## 5. Fake Model 如何替换真实模型

测试使用 LangChain Core 自带的 `FakeListChatModel`。它实现同一个 `BaseChatModel` 抽象，可通过 `create_app(..., model_override=fake)` 或 `create_chat_model(..., model_override=fake)` 注入。

Fake 的优先级高于 Provider 校验，所以测试可以故意配置一个不存在的 Provider，同时证明工厂确实返回注入对象。Health 看到 Fake 后只返回 `model_configured=true`，不会创建 `ChatOpenAI`。这使接口测试不需要真实 API Key，也不会访问外网。

## 6. 每个新增或修改文件的职责

| 文件 | 职责 |
| --- | --- |
| `pyproject.toml` | Python 3.12、P0 依赖、pytest 与 Ruff 配置 |
| `uv.lock` | 锁定完整可复现依赖图 |
| `.env.example` | 展示安全的配置变量名和占位值 |
| `.gitignore` | 忽略 `.venv`、测试与 Ruff 缓存等本地产物 |
| `src/repopilot/infrastructure/config.py` | 配置字段、校验、安全导出和显式加载入口 |
| `src/repopilot/infrastructure/model_factory.py` | Provider 边界、Fake 替换和配置完整性判断 |
| `src/repopilot/api/app.py` | FastAPI Application Factory 和依赖装配 |
| `src/repopilot/api/dependencies.py` | 从当前应用实例读取请求依赖 |
| `src/repopilot/api/routes/health.py` | `/health` 路由和公开响应组装 |
| `src/repopilot/api/routes/__init__.py` | 路由包标识 |
| `src/repopilot/schemas/health.py` | Health API 的严格响应 Schema |
| `tests/unit/test_config.py` | 配置优先级、隔离、脱敏和边界校验 |
| `tests/unit/test_model_factory.py` | 注入、异常、参数传递和零网络构造 |
| `tests/integration/test_health_api.py` | Application Factory 到 HTTP 响应的离线集成验证 |
| `docs/decisions/CODE_REUSE_LOG.md` | 记录 P0 实际借鉴、替换和未复制结论 |
| `docs/learning/P0-foundation-and-model.md` | 本阶段学习闭环 |
| `docs/interview/P0-questions.md` | 20 道 P0 面试主问题及追问 |
| `docs/interview/answers/P0-answers.md` | 与真实源码绑定的答案和错误示例 |

## 7. 为什么此阶段不实现 Agent

Agent Loop 会同时引入消息状态、工具调用、循环终止、错误反馈和模型行为等变量。如果配置、依赖注入和 API 组合根尚未稳定，失败时无法判断问题来自环境、模型客户端还是工作流。P0 先把这些非 Agent 基础设施变成已测试的确定性边界，为 P1 留下一个小而可靠的起点。

这也是阶段门禁的意义：P0 完成后停止，不能因为模型对象已经可创建就顺手调用模型或创建 StateGraph。

## 8. 与 KamaClaude S0 的区别

定向阅读 S0 和其配置、应用组合及 LLM Provider 后，本项目只保留三个原则：配置优先级可测试、Provider 可注入、应用依赖在明确入口组合。

RepoPilot P0 不迁移 KamaClaude 的 daemon、TCP/JSON-RPC、EventBus、TUI、Anthropic 原生消息解析或 AgentLoop。这里使用 FastAPI Application Factory 作为单进程 Demo 的组合根，使用 Pydantic Settings 进行字段级校验，使用 LangChain `BaseChatModel` 作为模型边界。相关代码均按 RepoPilot 的依赖和测试重新编写，没有复制参考源码。

## 9. 当前设计牺牲的 daemon 能力

移除 daemon 后，P0 不具备客户端断开后任务继续、多客户端共享运行状态、跨进程事件订阅、后台任务监管、socket 生命周期和本地 TUI 多路接入。对当前 Health Demo 而言，这些能力没有消费者，引入它们只会扩大失败面。

未来若出现明确的长任务和断线恢复需求，应优先用持久化 Checkpointer、任务队列或部署平台能力验证需求，而不是默认重建自定义 TCP daemon。

## 10. 依赖选择的真实取舍

最初尝试使用当前 `langchain` 元包的统一初始化入口，但依赖解析会传递安装 LangGraph，与“P0 不加入 LangGraph”的硬约束冲突。因此最终使用 `langchain-core` 加 `langchain-openai`：业务可见边界仍是 `BaseChatModel`，具体 `ChatOpenAI` 只出现在基础设施工厂内，锁文件中没有 LangGraph。

`langchain-core` 会传递安装 LangSmith 基础客户端，但 P0 没有配置、初始化或实现任何 LangSmith Trace。它只是依赖图中的传递包，不代表本阶段启用了追踪功能。

## 11. 必须由我亲手复写的三段代码

1. `AppSettings` 的 temperature、timeout、空 API Key 校验和 `safe_dump()`。复写后说明每个边界防止什么错误。
2. `create_chat_model()` 的“Fake 优先—Provider 校验—必需配置校验—构造但不调用”顺序。复写时不要看原文件。
3. `create_app()` 到 `health()` 的依赖流。亲手把 Settings 注入应用实例，并写一个断言 Health 不含 API Key 的测试。

## 12. 主动修改练习

新增非敏感配置 `build_version`，默认值为 `dev`，并让 Health 响应包含它。需要同步修改 Settings、Health Schema、Route、`.env.example` 和集成测试。完成后回答：为什么不应把 Git 命令或文件读取放进 Health 路由实时执行？

验收：所有原测试继续通过，新增响应字段有严格 Schema，且没有引入全局配置单例。

## 13. 故障注入练习

把 `REPOPILOT_MODEL_TIMEOUT_SECONDS` 设为 `0`、`-1`、`301` 和非数字文本，分别启动应用并观察 Pydantic 错误位置。然后在测试中 monkeypatch `ChatOpenAI` 让其构造时抛出异常，确认 `/health` 仍不触发该异常，因为 Health 只检查配置完整性。

思考：若未来增加 `/ready` 并要求真实探测模型，应如何设置短超时、错误脱敏和显式开关，使其与当前无网络 `/health` 分离？本阶段只回答，不实现。

## 14. 一分钟面试口述稿

“RepoPilot 的 P0 先解决可组合和可测试，而不是直接写 Agent。我用 Pydantic Settings 把 `REPOPILOT_` 环境变量转成带边界校验的配置，API Key 用 `SecretStr`，诊断导出还会主动排除密钥和 Base URL。模型侧以 LangChain Core 的 `BaseChatModel` 为边界，具体 OpenAI 类只存在于工厂；测试可以优先注入 `FakeListChatModel`，所以不需要真实密钥和网络。FastAPI 使用 Application Factory，每个测试都能传入自己的 Settings 和 Fake，`/health` 只返回公开配置摘要与 `model_configured`，不会调用模型。依赖方面我没有使用会传递引入 LangGraph 的 LangChain 元包，而是选择 Core 加 Provider 集成，并用 `uv.lock` 固定依赖。这样 P1 可以建立在一个经过 17 项离线测试验证的组合根上，同时 P0 没有提前实现任何 Agent 工作流。”
