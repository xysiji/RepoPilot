# RepoPilot P0 面试题答案

## 一、概念题

### 1. Pydantic `BaseSettings` 解决了什么问题？

- 标准答案：它把默认值、`REPOPILOT_` 环境变量、可选 dotenv 和显式构造参数汇合成一个强类型对象，并在应用装配前校验范围、格式和空值。
- 真实源码：`src/repopilot/infrastructure/config.py::AppSettings`、`load_settings`；测试见 `tests/unit/test_config.py`。
- 常见错误回答：“它只是读取 `.env`。”这忽略了类型转换、来源优先级和校验。
- 面试表达：我把不可靠的字符串配置在组合根边界转换成已验证对象，后续代码只消费明确类型。
- 追问 1 答案：`SecretStr` 防止普通展示直接泄密，`safe_dump()` 再从诊断数据中删除敏感字段；两层分别防误打印和误导出。
- 追问 2 答案：import 时读取会让测试收集、脚本导入和多 App 实例隐式依赖本机环境，也无法方便替换配置。

### 2. 什么是 Application Factory？

- 标准答案：它是每次调用都根据传入依赖创建一个 FastAPI 实例的函数。本项目的 `create_app()` 安装 Settings、可选 Fake Model 和 Router。
- 真实源码：`src/repopilot/api/app.py::create_app`。
- 常见错误回答：“就是把 `app = FastAPI()` 包进函数，其他全是全局单例。”这没有获得可替换性。
- 面试表达：Application Factory 是 P0 的 composition root，负责显式装配而不是承载业务逻辑。
- 追问 1 答案：测试可为每个用例创建独立 App，传入专属 Settings/Fake，不需要修改进程级全局对象。
- 追问 2 答案：不是绝对禁止无状态常量或 Router；禁止的是读取真实环境、持有可变状态且不可替换的复杂全局对象。

### 3. `BaseChatModel` 承担什么角色？

- 标准答案：它是 LangChain Chat Model 的统一抽象，让调用方和测试依赖稳定接口，而不是直接依赖某个 Provider 类。
- 真实源码：`src/repopilot/infrastructure/model_factory.py` 的参数与返回类型。
- 常见错误回答：“它会自动选择最好的模型。”抽象接口不负责业务选型。
- 面试表达：我把 Provider 特有构造压缩在基础设施边界，对外只暴露 `BaseChatModel`。
- 追问 1 答案：工厂的职责就是把通用配置翻译成具体客户端，所以内部出现 `ChatOpenAI` 合理；其他层不应导入它。
- 追问 2 答案：遵守同一抽象才能无条件替换，并让后续调用代码不需要为 Fake 添加特殊分支。

### 4. `/health` 与真实模型探测的区别是什么？

- 标准答案：当前 Health 证明 API 正常、配置已成功加载，并报告配置完整性；它不证明远端模型、密钥权限或网络一定可用。
- 真实源码：`src/repopilot/api/routes/health.py::health`、`model_factory.py::is_model_configured`。
- 常见错误回答：“200 就代表 OpenAI 一定可调用。”
- 面试表达：我把轻量存活/配置检查与有外部副作用的深度就绪检查分开。
- 追问 1 答案：真实调用会增加延迟、成本、限流和泄漏风险，并让基础监控依赖第三方稳定性。
- 追问 2 答案：可新增显式 `/ready` 或受控诊断命令，设置短超时、错误脱敏和开关；不改变 `/health` 的无网络语义。

### 5. `uv.lock` 与 `pyproject.toml` 的区别是什么？

- 标准答案：`pyproject.toml` 描述项目约束和直接依赖范围；`uv.lock` 记录解析后的精确直接/传递版本，支持重现环境。
- 真实源码：`pyproject.toml`、`uv.lock`。
- 常见错误回答：“二者都是依赖列表，保留一个就行。”
- 面试表达：声明文件表达兼容意图，锁文件固定一次已验证的完整解析结果。
- 追问 1 答案：宽范围会在不同日期解析出不同传递版本，可能导致同一提交行为变化。
- 追问 2 答案：项目门禁要求 Python 3.12，`<3.13` 可阻止工具误用系统 3.14；升级解释器应独立评估。

## 二、源码题

### 6. `create_chat_model()` 的执行顺序是什么？

- 标准答案：先返回 override，再验证 Provider，再验证 Provider 必需配置，最后构造 `ChatOpenAI` 并以 `BaseChatModel` 返回；不调用模型。
- 真实源码：`src/repopilot/infrastructure/model_factory.py::create_chat_model`。
- 常见错误回答：“先建真实模型，再看是否有 Fake。”这会让离线测试失去意义。
- 面试表达：工厂把可替换性放在最前，把确定性错误放在外部构造之前。
- 追问 1 答案：Fake 应完全替代真实路径；先校验未知 Provider 会让与 Provider 无关的测试仍被真实配置阻塞。
- 追问 2 答案：函数没有 `invoke/stream`；`test_factory_constructs_base_chat_model_without_network_request` 还 patch `httpx.Client.send` 并断言未调用。

### 7. `safe_dump()` 为什么排除两个字段？

- 标准答案：API Key 本身是秘密；Base URL 也可能携带查询 token、租户或代理凭证，因此诊断导出同时排除二者。
- 真实源码：`src/repopilot/infrastructure/config.py::AppSettings.safe_dump`。
- 常见错误回答：“Base URL 永远公开，只隐藏 API Key 即可。”
- 面试表达：脱敏按数据可能承载的内容判断，不只按字段名称判断。
- 追问 1 答案：遮蔽值仍会暴露字段存在与格式，而且未来序列化行为可能变化；显式排除更符合最小披露。
- 追问 2 答案：用唯一秘密字符串构造 Settings，把安全导出转 JSON，断言秘密、查询 token 和两个字段名均不存在。

### 8. Settings 怎样传到 Health？

- 标准答案：`create_app()` 把具体 Settings 实例放入 `app.state.settings`；请求依赖 `get_settings()` 从当前 `request.app` 取回；路由接收强类型对象。
- 真实源码：`src/repopilot/api/app.py`、`src/repopilot/api/dependencies.py`、`src/repopilot/api/routes/health.py`。
- 常见错误回答：“路由每次自己读 `.env`。”这会重复解析并破坏一致性。
- 面试表达：配置在组合根加载一次，在请求链路显式获取，不被模块级单例隐藏。
- 追问 1 答案：实例已经完成类型转换和校验，传字典会丢失契约并诱发重复解析。
- 追问 2 答案：不会；每个 FastAPI 实例有自己的 `state`，集成测试可独立创建多个 App。

### 9. `extra="forbid"` 有什么价值？

- 标准答案：它让意外加入 Schema 未声明字段时立即失败，降低内部或敏感数据被顺手塞进响应的风险。
- 真实源码：`src/repopilot/schemas/health.py::HealthResponse`。
- 常见错误回答：“只是为了代码补全，对运行时没有作用。”
- 面试表达：Health 是公开契约，我选择默认拒绝未声明字段，避免响应面静默膨胀。
- 追问 1 答案：`response_model` 会验证和序列化输出，生成 OpenAPI 契约，并限制对外字段。
- 追问 2 答案：同步修改 Schema、Route 组装、`.env.example`（若来自配置）和集成测试的精确响应断言。

### 10. 测试如何与开发机 `.env` 隔离？

- 标准答案：直接构造 `AppSettings(..., _env_file=None)`；需要测 dotenv 时只传 `tmp_path` 下的专用文件。
- 真实源码：`tests/unit/test_config.py`。
- 常见错误回答：“CI 通常没有 `.env`，所以不用隔离。”
- 面试表达：测试显式控制配置来源，不把开发机偶然状态当测试前提。
- 追问 1 答案：`monkeypatch` 会在用例结束恢复环境，避免进程级环境变量跨测试污染。
- 追问 2 答案：显式构造参数优先；`test_direct_values_override_environment` 设置冲突值并断言构造值生效。

## 三、设计取舍题

### 11. 为什么没有依赖 `langchain` 元包？

- 标准答案：实际解析发现当前元包会传递引入 LangGraph，而 P0 明确禁止加入 LangGraph；因此改用 `langchain-core` 和最小 Provider 集成。
- 真实源码：`pyproject.toml`、`uv.lock`；决策说明见 `docs/learning/P0-foundation-and-model.md`。
- 常见错误回答：“LangChain 和 LangGraph 完全无关，装元包不会有影响。”
- 面试表达：我以锁文件的真实依赖图为准，让阶段边界优先于 API 使用偏好。
- 追问 1 答案：暂时不能使用元包提供的统一初始化入口；工厂要显式构造 Provider，但具体类仍被封装。
- 追问 2 答案：检查 `pyproject.toml` 无直接依赖，并搜索 `uv.lock` 不含 `name = "langgraph`；最终还检查源码无导入。

### 12. 单 Provider 为什么仍需要工厂？

- 标准答案：工厂集中处理必要配置、清晰错误、具体类构造和测试 override。它解决边界问题，不等于要提前做多 Provider 插件系统。
- 真实源码：`src/repopilot/infrastructure/model_factory.py`。
- 常见错误回答：“只有一个 Provider 就直接在每个路由 new `ChatOpenAI`。”
- 面试表达：先保留一个薄的变化边界，不为尚不存在的第二实现设计 Registry。
- 追问 1 答案：第二 Provider 被确认、构造参数差异开始产生分支且有真实测试后，再考虑映射或策略对象。
- 追问 2 答案：API 和未来业务只接触 `BaseChatModel`；`ChatOpenAI` 导入只在 infrastructure 工厂。

### 13. 为什么应用启动时不立即创建模型？

- 标准答案：Health 不使用模型，启动构造只会增加密钥要求和客户端初始化失败面；P0 通过配置完整性判断提供可观察状态。
- 真实源码：`src/repopilot/api/app.py::create_app` 没有调用 `create_chat_model`。
- 常见错误回答：“为了启动更快，所以永远不需要验证模型配置。”
- 面试表达：按消费者需要延迟构造，同时在真正使用边界保留明确的 fail-fast 错误。
- 追问 1 答案：未知 Provider 或缺失密钥会在首次调用工厂时明确失败；Health 先以 `model_configured` 报告完整性。
- 追问 2 答案：它只表示有 Fake，或已知 Provider 的必需本地字段存在，不验证网络、权限、余额和模型名。

### 14. 为什么不实现 daemon、EventBus 或数据库？

- 标准答案：P0 没有后台 Agent、事件消费者或持久状态，这些设施没有当前消费者，会扩大依赖与测试面。
- 真实源码：`docs/architecture/ARCHITECTURE.md`、`docs/development/DEVELOPMENT_PLAN.md`，以及 P0 中不存在相应实现。
- 常见错误回答：“这些技术不好，所以永远不应该使用。”
- 面试表达：我按现阶段产品闭环裁剪能力，而不是按技术偏好永久否定它们。
- 追问 1 答案：牺牲断线继续、多客户端共享运行、跨进程事件订阅和后台监管。
- 追问 2 答案：真实长任务、断线恢复、多实例协调或稳定事件消费者出现，并有可验证验收标准时。

### 15. 为什么安全边界必须由代码校验？

- 标准答案：Prompt 和文档是软约束，不能阻止非法输入进入运行时；Pydantic 范围校验和工厂白名单能确定性拒绝错误状态。
- 真实源码：`src/repopilot/infrastructure/config.py`、`model_factory.py`。
- 常见错误回答：“在系统提示词写一句不要越界就足够。”
- 面试表达：模型负责生成建议，Python 代码负责可判定的允许/拒绝条件。
- 追问 1 答案：可能导致 Provider 拒绝、不可预测输出或费用/延迟异常；因此入口直接拒绝。
- 追问 2 答案：默认模型名、当前只接 OpenAI、Health 展示哪些非敏感字段属于产品策略，可随阶段决策调整。

## 四、异常与排错题

### 16. timeout 校验错误如何定位？

- 标准答案：先读 Pydantic 错误字段和值，再检查显式构造参数、`REPOPILOT_MODEL_TIMEOUT_SECONDS` 环境变量和传给 `load_settings` 的 env 文件。
- 真实源码：`src/repopilot/infrastructure/config.py`；边界测试见 `test_invalid_model_limits_are_rejected`。
- 常见错误回答：“捕获所有异常，然后使用 30 秒默认值继续。”
- 面试表达：保留来源和校验错误，让部署配置问题在组合边界可见。
- 追问 1 答案：用最小脚本分别传 `_env_file=None`、专用 env 文件和显式值，结合环境变量检查逐层隔离；构造参数优先级最高。
- 追问 2 答案：静默回退会让操作者以为配置已生效，可能隐藏严重超时策略错误并增加排查成本。

### 17. 未知 Provider 应怎样处理？

- 标准答案：工厂抛出包含实际值和支持列表的 `UnknownModelProviderError`，调用边界决定如何展示；不猜测、不回退。
- 真实源码：`src/repopilot/infrastructure/model_factory.py`、对应单元测试。
- 常见错误回答：“拼错时自动使用默认 OpenAI。”
- 面试表达：Provider 是影响外部调用和费用的显式选择，未知值采用 fail-closed。
- 追问 1 答案：自动回退可能把请求和数据发送到错误供应商，并掩盖部署错误。
- 追问 2 答案：Fake override 是完整依赖替换，不需要真实 Provider；优先返回 Fake 才能保持离线测试独立。

### 18. Health 泄漏 API Key 如何排查？

- 标准答案：检查 `HealthResponse` 字段、Route 构造、依赖返回值、异常处理和日志；确认没有直接序列化完整 Settings。
- 真实源码：`src/repopilot/schemas/health.py`、`api/routes/health.py`、`tests/integration/test_health_api.py`。
- 常见错误回答：“把 JSON 中的 `api_key` 字段重命名就行。”
- 面试表达：从数据源、公开 Schema、序列化和回归测试四层收紧最小披露。
- 追问 1 答案：泄漏可能发生在 HTTP 响应、异常或调试导出，日志过滤只能覆盖其中一条路径。
- 追问 2 答案：使用随机唯一秘密作为 Key 和 URL token，断言整个 `response.text` 都不包含该值，而不只检查字段名。

### 19. 如何定位并阻断真实网络调用？

- 标准答案：确保测试注入 Fake；在工厂单测 patch HTTP 发送方法，在 API 集成测试 patch Provider 构造并令其立即失败；再检查调用栈。
- 真实源码：`tests/unit/test_model_factory.py`、`tests/integration/test_health_api.py`。
- 常见错误回答：“用 `test-key`，请求失败就说明没有真正调用。”
- 面试表达：离线性要用负断言证明，而不是靠无效凭证间接猜测。
- 追问 1 答案：客户端仍可能发出 DNS/HTTP 请求、泄漏元数据、拖慢测试或触发限流，只是最终鉴权失败。
- 追问 2 答案：单元层断言构造不发送 HTTP；接口层断言 Health 不构造 Provider；未来 Agent 集成层统一注入 Fake。

### 20. pytest 通过但 Ruff 失败怎么办？

- 标准答案：按 Ruff 定位修复格式、导入或代码问题，再重新运行 pytest、`ruff check` 和 `ruff format --check`，不改变测试语义。
- 真实源码：`pyproject.toml` 的 Ruff 配置和全体 `src/`、`tests/`。
- 常见错误回答：“功能没坏就关闭该规则，或把目录加入 exclude。”
- 面试表达：行为验证和静态质量是两道独立门禁，必须同时满足。
- 追问 1 答案：删除规则会降低整个项目的一致性保证，排除产品目录则让门禁失真；只有经过团队决策的规则冲突才调整配置。
- 追问 2 答案：运行 `ruff format --check`、`git diff --check`、`git status --short`，并审查敏感信息、真实网络、P1 内容和过度抽象。
