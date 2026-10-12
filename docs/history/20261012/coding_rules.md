# OpsPilot Coding Rules

## 1. 适用范围与优先级

本文约束 OpsPilot 三阶段重构中的 Python 代码、数据库迁移、测试、Benchmark 和生成工件。规则优先级为：可验证正确性 > 数据与接口兼容 > 简洁性 > 扩展性。不要为了展示技术栈提前实现 `plan_new.md` 已明确排除的组件。

## 2. 总体开发规则

- Python 版本保持 3.11+，沿用 `src/` layout、Pydantic v2、FastAPI、LangGraph、pytest 和 Ruff；
- 每个任务完成真实的“输入 -> 实现 -> 接入 -> 测试 -> Benchmark/验证 -> 工件”链，不提交只有抽象接口没有调用方的骨架；
- 复用现有 DeepRCA 算法和工具行为，迁移时先写 characterization test，再移动代码；
- 新代码进入 `src/opspilot/`；迁移期 `src/deeprca/` 只允许兼容适配和必要修复，禁止复制两份长期分叉的核心逻辑；
- 不把旧框架能力描述为 OpsPilot 新增贡献；提交和文档必须区分“迁移/复用”与“新增”；
- 所有目标指标在真实运行前写成 `target`，禁止用占位数字冒充实测值；
- 不自动修改 frozen test 标签，不删除退化 case，不手填 predictions。

## 3. 分层与依赖方向

允许的依赖方向：

```text
api -> runtime -> graph/agents -> tools/evidence/rca
                 |                       |
                 +-> models <------------+

runtime -> persistence
runtime -> redis queue adapter
evaluation -> public runtime/system interface
mock_env -> models（不得反向依赖生产 runtime）
```

约束：

- `models` 不导入 FastAPI、SQLAlchemy、Redis、LangGraph 或具体 Tool；
- Agent 不直接创建数据库/Redis/HTTP 客户端；
- 路由不直接执行 LangGraph，也不写 SQL；只能调用 application/runtime service；
- Tool 通过统一 Executor 调用外部系统，不能在 Agent 内散落 `httpx` 与重试逻辑；
- Evaluation 只能调用系统公开入口或稳定 application interface，不能读取内部变量伪造输出；
- Mock Environment 只提供可重复输入和故障，不包含评测答案判断逻辑。

任何跨层例外必须在 `docs/architecture.md` 的架构决定表中说明。

## 4. 命名与代码风格

- 包、模块、函数、变量使用 `snake_case`；类型使用 `PascalCase`；常量使用 `UPPER_SNAKE_CASE`；
- 统一使用 `kafka`，不在新代码中继续 `mafka` 拼写；旧接口只在兼容层映射；
- 统一领域值：`change | metric | log | trace | topology | db | redis | kafka | rpc | resource`；
- ID 字段必须带语义：`run_id`、`step_id`、`tool_call_id`、`evidence_id`，禁止用无上下文的 `id` 穿过模块边界；
- 时间一律为带时区 UTC；API/JSON 使用 ISO 8601，数据库使用 timezone-aware timestamp；
- 金额、耗时、比例的单位写入字段名，例如 `latency_ms`、`timeout_seconds`；
- 公共函数和 schema 写简短 docstring，解释契约和失败条件，不写逐行翻译；
- 函数优先保持单一职责；若一个函数同时做 I/O、重试、转换和持久化，应拆到明确层；
- 不新增无调用方的 factory、manager、base class 或通用框架。

## 5. Schema 与兼容规则

- 跨模块、API、队列、数据库 JSON 和 Benchmark case 一律使用显式版本化 schema；
- Pydantic 模型默认拒绝未知字段，只有兼容入口可以显式 `extra="ignore"`；
- 枚举值落库后视为公共协议，变更必须通过 migration/adapter；
- 不直接把 ORM model 当 API response；domain schema 与 persistence model 分离；
- 所有 JSON 输出必须可稳定序列化；集合先排序，浮点指标定义舍入策略；
- `RootCauseResult` 必须提供稳定的 `root_cause_type`，自由文本只用于展示；
- `Evidence` 必须表示观察事实，不能把“数据库就是根因”这类推理结论伪装为观测；
- 兼容 `/api/v1/analyze` 时必须通过 adapter 映射到新 Run，禁止维护第二套执行链。

Schema 变更检查清单：

1. 更新模型与 schema version；
2. 更新 contract test；
3. 若已落库，新增向前 migration；
4. 若影响数据集，发布新 dataset version，不原地覆盖 v1；
5. 记录对旧 API、Checkpoint 和 artifact reader 的影响。

## 6. 异步与外部 I/O

- FastAPI 路由保持短操作：校验、调用 service、返回；不在请求协程内等待完整 RCA；
- 不在 async 函数内调用阻塞网络/数据库 API；选用 async client，或在明确边界使用线程池；
- HTTP、LLM、Redis 和数据库连接由应用生命周期管理，不按每次工具调用重复创建；
- 所有外部调用必须有 timeout；禁止裸 `except Exception: pass`；
- 并发必须有上限，优先使用配置和 semaphore；禁止无界 `asyncio.gather`；
- 自动测试不访问付费 LLM 或公网服务，使用 fake LLM、fake clock 和确定性 seed；
- Mock 随机数必须接受 seed，同一 case 重跑应得到一致 ground truth。

## 7. Tool Registry 与 Executor

每个 Tool 注册以下元数据：

```text
name, version, description
input_schema, output_schema
timeout_seconds, max_attempts
idempotent, side_effect
```

执行顺序固定为：

```text
lookup -> input validation -> idempotency lookup -> timeout/retry
-> execute -> output validation -> persist ToolExecution -> return ToolResult
```

规则：

- Agent 只能按注册名选择 Tool，不导入具体 provider 实现；
- 读取型 Tool 的 `side_effect` 必须为 `false`；MVP 不接入写操作 Tool；
- `tool_call_id` 基于 `run_id + step_id + tool_name + tool_version + normalized_args` 稳定计算；
- 参数规范化必须排序 key、去除非语义默认值，且不得包含 secret；
- 成功记录已存在时直接复用结果；失败记录是否重试由错误类型和 attempt 决定；
- ToolResult 必须区分空数据成功与执行失败；不能用 `{}` 同时表达两者；
- 原始大结果存引用，Trace 只保存摘要和 digest，避免日志泄密或膨胀。

## 8. Run、Step 与数据库事务

- PostgreSQL 是唯一持久事实源；Redis Queue 消息不是状态；
- Run/Step 状态只能通过 TaskManager/Repository 的显式方法推进，禁止业务代码任意赋值；
- 每次状态迁移验证当前状态，使用乐观版本或条件更新防止并发覆盖；
- `request_id` 建唯一约束，不能只靠“先查再插”实现幂等；
- Step 唯一键至少覆盖 `run_id + step_name + execution_version`；
- Step 成功、输出引用和 Checkpoint 在同一事务提交；事务完成后才能把后续 Step 视为可恢复；
- 数据库写成功但队列写失败时，Run 仍保留 `QUEUED`，由 recovery scan 补投；
- 队列重复投递必须安全：Worker 读取数据库终态或已完成 Step 后直接跳过；
- migration 只向前新增，不通过删库作为正常升级方案；测试可使用独立临时数据库。

## 9. Checkpoint 与恢复

- Checkpoint 只在稳定 Step 边界保存，不序列化连接、client、coroutine 或不可重建对象；
- `state_json` 必须包含 `schema_version`、`graph_version` 和恢复所需的最小数据；
- 大型 Tool 原始输出使用 `raw_ref`，不无限复制进每个 Checkpoint；
- 恢复从最新合法 Checkpoint 的下一个 Step 开始；没有成功 Checkpoint 的当前 Step允许重跑；
- 恢复前校验 Checkpoint 版本；不兼容时明确失败，不静默丢字段；
- 每次恢复记录 RuntimeEvent 和 `recovered_count`；
- 恢复测试必须真的终止 Worker 进程或在受控边界抛出中断，不能直接伪造 `SUCCEEDED` 状态；
- 第一版不宣称 exactly-once，只宣称 at-least-once delivery + idempotent execution。

## 10. 错误、Retry 与降级

错误至少分两类：

- Retryable：Timeout、HTTP 5xx、RateLimit、临时 Redis/DB 连接错误；
- Permanent：InvalidSchema、UnknownTool、PermissionDenied、InvalidAlert、CheckpointVersionMismatch。

规则：

- 仅 RetryableError 进入有限重试；次数、backoff 和 jitter 来自配置；
- 不捕获异常后返回“正常空结果”；错误必须进入 ToolExecution/Step/Run；
- 单个非关键 Tool 失败可生成 degraded report，但报告必须显式 `degraded=true` 并列出缺失证据；
- Retry 耗尽后 Run 进入 `FAILED`；第一版不额外实现 DLQ；
- 用户可重试必须创建新的 attempt/event，保留原始失败证据。

## 11. RCA 与 Evidence 规则

- 处理顺序保持：Tool data -> deterministic detection/filter/rule -> Evidence -> Root Cause Agent；
- 阈值和规则从版本化配置加载，不散落在 prompt 和多个 Agent 中；
- 输入样本不足时返回 `insufficient_data`，不强行计算 IQR；
- Root Cause Agent 只接收 Top-N Evidence、反证、计划和预算，不接收全部原始日志；
- LLM 输出必须经过 schema validation；失败时使用确定性候选/fallback；
- 不保存 Chain-of-Thought，只保存输入摘要、输出、证据引用和简短 decision rationale；
- 正常/噪声 case 允许输出“无明确故障根因”，不能强迫每次都给出故障结论。

## 12. 测试规则

测试分层：

| 类型 | 覆盖范围 | 外部依赖 |
|---|---|---|
| unit | 算法、状态迁移、幂等 key、指标公式 | 无 |
| contract | API/schema/Tool 输入输出兼容 | fake |
| integration | API -> DB -> Queue -> Worker -> Report | 本地 Postgres/Redis/Mock |
| recovery | Worker 中断、重复投递、工具超时 | Compose 或受控多进程 |
| evaluation | dataset loader、runner、metrics、artifact | fake/system adapter |
| regression | 修复过的真实诊断或运行时失败 | 固定 fixture |
| smoke | Compose 完整链路 | 本地容器，不访问付费 API |

要求：

- 新模块必须有 unit test，跨模块行为必须有最小 integration test；
- 迁移现有算法先保留原测试，再补新 namespace 下的 contract；
- 时间、UUID、随机数和 LLM 输出可注入，测试不得依赖真实当前时间；
- 异步测试不得使用任意 `sleep` 等待完成，使用状态轮询和明确 deadline；
- 测试名描述行为，例如 `test_duplicate_request_returns_existing_run`；
- 修复过的真实失败转为 `tests/regression/` 可执行用例；
- 测试失败时修实现，不能降低 frozen 标签或删除 case。

## 13. Benchmark 纪律

- dataset 文件包含 `name/version/split/case_count/schema_version`；
- dev 可用于调参，frozen test 在阶段验收时运行，运行后不继续针对同一 test 调参；
- baseline 和增强方案使用同一 dataset、seed、mock 状态、超时和指标公式；
- `predictions.jsonl` 必须由 runner 直接写出；
- metrics 同时保存分子、分母、macro/micro 口径和无法评分的 case；
- 延迟统计注明是否包含排队时间、恢复等待和模型调用；默认 E2E 包含全部；
- 失败至少分类为 planning、tool、evidence、root_cause、runtime、schema；
- 报告必须展示退化 case，不只展示平均提升；
- 任何真实模型实验记录模型名、endpoint 类型、温度、token、运行日期和成本口径，但绝不提交 API key。

## 14. 配置、安全与可观测

- secret 只从环境变量/secret manager 读取；日志、artifact、queue message 不得包含 key；
- 配置由 Settings 加载并在 Run 中保存 `config_version`，禁止全局可变 dict；
- 日志使用结构化字段：`run_id/step_id/tool_call_id/event/status/duration_ms`；
- 不记录完整 prompt、原始敏感日志或用户数据；必要内容脱敏后保存 digest/ref；
- RuntimeEvent 记录事实事件，不在日志文本中推断状态；
- health 与 readiness 区分：API 存活不等于 PostgreSQL/Redis 可接受任务；
- 生成的 `artifacts/` 默认不提交；精选、脱敏的报告可复制到文档证据目录，但需记录来源 evaluation_id。

## 15. 质量门禁与完成定义

实现阶段的标准命令目标为：

```bash
python -m pip install -e '.[dev]'
ruff check src tests
pytest -q tests/unit tests/contract
pytest -q tests/integration tests/recovery
python -m opspilot.evaluation.cli run --config benchmarks/configs/hybrid.yaml
docker compose up --build --abort-on-container-exit
```

命令在对应入口实现前属于目标接口，不得声称已运行。

单个任务只有同时满足以下条件才可标记完成：

- 行为和失败分支已实现；
- 单元/契约/必要集成测试真实通过；
- 有同集 Benchmark 或风险匹配的验证结果；
- 生成标准工件并记录真实命令；
- 没有无关修改、secret、手填 prediction 或被静默跳过的失败；
- `docs/task_board.md` 只更新当前任务状态和实测结果。

