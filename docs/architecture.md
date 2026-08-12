# OpsPilot Architecture

## 1. 文档目的

本文描述从当前 DeepRCA 到 OpsPilot 首个完整版本的目标结构、模块边界、数据契约和迁移顺序。`plan_new.md` 是范围依据；旧 `plan.md` 只作为未来扩展参考。本文不代表相关模块已经实现。

## 2. 当前架构事实

### 2.1 当前在线链路

```text
POST /api/v1/analyze
-> AlertEvent 校验
-> API 进程创建 asyncio background task
-> LangGraph
   -> intake
   -> planner（按 alert_type 选择固定维度）
   -> dispatcher（L1 分析并发 + L2 Expert）
   -> collector（EvidencePool 摘要）
   -> root_cause（算法/规则优先，LLM 或 fallback）
   -> reporter
-> Redis TTL 状态 / 进程内降级字典
-> GET result / WebSocket
```

主要入口与扩展点：

| 位置 | 当前职责 | 迁移用途 |
|---|---|---|
| `src/deeprca/main.py` | FastAPI 应用入口 | API 兼容入口 |
| `src/deeprca/api/routes.py` | 提交、状态、结果、反馈、WebSocket；进程内启动图 | 替换为 Run API 与 Runtime service 调用 |
| `src/deeprca/graph/main_graph.py` | 六节点 LangGraph | 保留工作流语义，在 Step 边界接入 Checkpoint |
| `src/deeprca/agents/coordinator.py` | 解析、规划、并发、汇聚、报告 | 拆分为可测试节点，不再负责运行时持久化 |
| `src/deeprca/agents/root_cause.py` | 确定性分析、规则、LLM/fallback | 迁移为 Root Cause Agent |
| `src/deeprca/graph/subgraphs/` | DB/Redis/Kafka/RPC Expert | 第一阶段降级为领域 Tool，不继续扩张 Agent 数量 |
| `src/deeprca/tools/` | 外部数据查询 | 接入 Tool Registry/Executor |
| `src/deeprca/detection/` | IQR、波动、对比、过滤、规则 | 迁入 `opspilot/rca` 并保持行为回归 |
| `src/deeprca/mock_env/` | 8 个故障场景及模拟 API | 升级为版本化场景数据源 |

### 2.2 当前关键缺口

- API、任务执行和状态管理耦合在同一进程；进程退出会丢失执行上下文；
- Redis 既承担状态又可退回本地内存，无法作为可靠事实源；
- 没有 Run/Step 状态机、Checkpoint、恢复、请求幂等和工具执行幂等；
- 工具各自处理 HTTP、超时和异常，没有统一注册、记录和重试入口；
- `SubAgentResult` 缺少标准 `status/tool_calls/latency_ms`，领域命名也不统一；
- RCA 的部分算法读取 `alert.metrics`，但 API 的 `AlertEvent` 契约并未声明该字段；
- 现有场景和 smoke test 不等于 Benchmark：缺少固定 split、明确标签、逐 case 输出、指标公式和失败回归。

## 3. 目标架构

```text
                              查询状态/结果
Client --------------------------------------------------------+
  |                                                            |
  | POST /api/v1/runs                                          v
  v                                                     FastAPI Gateway
FastAPI Gateway                                                |
  | 校验 + request_id 幂等                                     |
  v                                                            |
PostgreSQL: Run(QUEUED) <--------------------------------------+
  |
  | enqueue run_id
  v
Redis Queue ---> Worker ---> OpsPilot LangGraph Workflow
                    |          |
                    |          +-> Coordinator Agent
                    |          +-> Tool Registry/Executor
                    |          +-> Deterministic RCA + Evidence
                    |          +-> Root Cause Agent
                    |
                    +-> PostgreSQL: Step / ToolExecution / Checkpoint
                    +-> PostgreSQL: DiagnosisReport / RuntimeEvent

Dataset + RunConfig -> Evaluation Runner -> System Predictions
                                         -> Metrics + Failures
                                         -> Regression Cases
                                         -> artifacts/evaluations/<id>/
```

PostgreSQL 是 Run 状态和执行结果的唯一事实源。Redis 只传递待执行的 `run_id`；队列消息丢失或重复都不能改变最终事实。Worker 每完成一个可恢复 Step，先在同一数据库事务中写 Step 输出和 Checkpoint，再推进 Run 状态。

## 4. 预期项目目录结构

当前 `src/deeprca/` 在迁移期间保留；`src/opspilot/` 是目标命名空间。第一、二阶段使用兼容适配，第三阶段统一导入并删除不再使用的重复实现。

```text
DeepRCA-Agent-master/
├── src/
│   ├── deeprca/                    # 迁移期旧实现；最终仅保留兼容层或移除
│   └── opspilot/
│       ├── api/
│       │   └── routes/             # runs、evaluations、WebSocket 路由
│       ├── agents/                 # 仅 Coordinator 与 Root Cause Agent
│       ├── config/                 # Settings 与版本化运行配置
│       ├── models/                 # Alert/Run/Step/Tool/Evidence/Report/Eval schema
│       ├── runtime/                # TaskManager、Worker、Checkpoint、Retry、Idempotency
│       ├── graph/                  # LangGraph state、nodes、workflow 组装
│       ├── tools/                  # Registry、Executor、领域查询 Tool
│       ├── evidence/               # Evidence 标准化、去重、排序、池化
│       ├── rca/                    # IQR、波动、对比、过滤、规则
│       ├── persistence/
│       │   └── repositories/       # PostgreSQL models、事务与仓储
│       ├── evaluation/             # dataset、runner、metrics、regression、report
│       └── mock_env/
│           └── scenarios/          # 场景加载与故障注入契约
├── benchmarks/
│   ├── configs/                    # baseline/hybrid 运行配置
│   └── datasets/rca/v1/            # dev 与 frozen test 数据
├── migrations/
│   └── versions/                   # PostgreSQL schema migrations
├── tests/
│   ├── unit/                       # 纯函数和单模块测试
│   ├── contract/                   # API、schema、Tool 契约
│   ├── integration/                # API -> Queue -> Worker -> DB
│   ├── recovery/                   # 中断、重试、幂等恢复
│   ├── evaluation/                 # 指标公式、runner、报告
│   ├── regression/                 # 真实失败固化用例
│   ├── smoke/                      # Compose 端到端验证
│   └── fixtures/                   # fake LLM、固定时钟和数据样本
├── artifacts/                      # 生成工件；实现阶段加入 gitignore
├── scripts/                        # 可复现开发、迁移、评测入口
├── docs/
│   ├── project_brief.md
│   ├── architecture.md
│   ├── coding_rules.md
│   └── task_board.md
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

目前只创建目录，不放置 `__init__.py`、schema、migration 或脚本，避免把规划骨架误认为实现。

## 5. 核心模块契约

| 模块 | 为什么需要 | 输入 | 输出 | 主要失败 |
|---|---|---|---|---|
| Run API | 将 HTTP 生命周期与长任务解耦 | `CreateRunRequest` | `RunAccepted` | 非法告警、重复 request、DB 不可用 |
| TaskManager | 统一创建、查询和推进 Run | Alert、request_id、config version | 持久化 Run/Step | 非法状态迁移、并发更新冲突 |
| Redis Queue | 低延迟传递待执行任务 | `run_id` | Worker 收到 `run_id` | 消息重复、Redis 暂时不可用 |
| Worker | 领取并执行 Run | `run_id` | Step/Checkpoint/Report | 进程退出、工具异常、超时 |
| Coordinator Agent | 规划需要调用的领域 Tool | Alert、已有证据、预算 | `AnalysisPlan` | 空计划、未知工具、预算不足 |
| Tool Registry | 声明 Tool 元数据和 schema | ToolDefinition | 可查找 Tool | 名称冲突、版本不兼容 |
| Tool Executor | 统一校验、超时、重试、幂等和记录 | ToolCall | ToolResult + ToolExecution | retryable/permanent error |
| Evidence Pipeline | 将不同工具输出变为同一事实单元 | ToolResult | ranked `Evidence[]` | 缺字段、重复、低质量噪声 |
| Deterministic RCA | 先做可解释筛选和规则判断 | Metrics/Evidence | anomaly/rule candidates | 样本不足、阈值配置错误 |
| Root Cause Agent | 融合候选与证据生成报告 | Plan、Top Evidence、rule candidates | `DiagnosisReport` | LLM 超时、结构不合法；走 fallback |
| CheckpointManager | 保存和加载可恢复状态 | Step output、graph state | Checkpoint | 序列化失败、版本不兼容 |
| Evaluation Runner | 同集运行方案并保存结果 | Dataset、RunConfig | predictions/metrics/failures | 数据污染、运行不完整 |

## 6. 核心数据结构

这里只冻结字段含义；具体 Pydantic/ORM 实现在阶段一、二完成。

### 6.1 Run

```text
run_id, request_id(unique), alert_id
status: QUEUED | RUNNING | RETRYING | SUCCEEDED | FAILED | CANCELLED
current_step, attempt
graph_version, config_version
created_at, started_at, finished_at
last_error_code, last_error_message
recovered_count
```

允许的主要状态迁移：

```text
QUEUED -> RUNNING
RUNNING -> SUCCEEDED | FAILED | RETRYING | CANCELLED
RETRYING -> QUEUED | FAILED | CANCELLED
```

终态不可被普通 Worker 更新；任何重放都创建新 attempt/event，不篡改历史。

### 6.2 Step 与 Checkpoint

```text
Step:
step_id, run_id, step_name, status, attempt
input_ref, output_ref, started_at, finished_at, error_code
unique(run_id, step_name, execution_version)

Checkpoint:
checkpoint_id, run_id, completed_step, state_json
schema_version, created_at
```

恢复只跳过已经成功且输出完整的 Step。正在执行但没有成功 Checkpoint 的 Step 允许重跑，因此所有 Tool 调用必须有稳定 `tool_call_id`。

### 6.3 ToolDefinition、ToolCall 与 ToolResult

```text
ToolDefinition:
name, version, description, input_schema, output_schema
timeout_seconds, max_attempts, idempotent, side_effect=false

ToolCall:
tool_call_id = hash(run_id + step_id + tool_name + version + normalized_args)
tool_name, arguments, requested_at

ToolResult:
status, data, error_code, error_message, latency_ms, attempt
```

MVP 的所有领域 Tool 都是只读工具。未来若增加副作用 Tool，必须另行设计审批，不可复用默认执行路径直接上线。

### 6.4 Evidence

```text
evidence_id, run_id
source_type: metric | log | change | trace | topology | rule
source_name, service, observed_at
fact, severity, confidence
supports[], contradicts[]
raw_ref
```

Evidence 是事实，不是根因结论。`supports`/`contradicts` 只引用稳定的 `root_cause_type`，不能把自由文本作为评测主键。

### 6.5 EvaluationCase

```text
case_id, dataset_version, split
alert, scenario_seed
expected_root_cause_type
expected_evidence_types[]
expected_tools[]
is_fault, tags[]
```

首版数据集建议 36 个 case：DB、Redis、Kafka、RPC、Deploy/Resource、Normal/Noise 六类各 6 个；dev 每类 4 个，frozen test 每类 2 个。最终可以扩展到 50 个，但不能通过复制近似样本凑数。

## 7. 在线正常流与失败流

### 7.1 正常流

```text
CreateRunRequest
-> 事务内按 request_id 创建 Run(QUEUED)
-> enqueue(run_id)
-> Worker 获取 run_id
-> Run(RUNNING)
-> 执行 Step
-> 保存 Step output + Checkpoint
-> 重复直到 Report
-> Run(SUCCEEDED)
-> API/WS 读取 PostgreSQL 返回结果
```

### 7.2 Worker 中断恢复

第一版使用简单、可解释的恢复策略：服务启动或周期性 recovery scan 查找长时间停留在 `RUNNING` 且没有活跃执行标记的 Run，将其标记 `RETRYING` 后重新入队。恢复 Worker 读取最新 Checkpoint，从下一个未完成 Step 开始。

该方案刻意不在第一版引入 Lease/Heartbeat。若多 Worker 并发领取造成真实冲突，再根据实验结果增加所有权与租约，而不是预先设计完整分布式调度系统。

### 7.3 工具失败

- `Timeout/HTTP 5xx/RateLimit`：按配置有限重试，记录每次 attempt；
- `InvalidInput/UnknownTool/SchemaError`：永久错误，不重试；
- 可降级工具失败：保留错误 Evidence，继续用已有证据生成 degraded report；
- 必需工具失败且证据不足：Run 失败，保留最后 Checkpoint 和错误分类。

## 8. Benchmark 链路与指标

```text
Dataset(version + split) + RunConfig
-> 对每个 case 注入场景并执行真实系统
-> predictions.jsonl
-> 指标聚合
-> failures.jsonl
-> report.md
-> 修复后的失败进入 tests/regression
```

统一工件路径：

```text
artifacts/evaluations/<evaluation_id>/
├── manifest.json       # git sha（若可用）、dataset、split、config、命令、case 数
├── predictions.jsonl   # 系统逐 case 输出
├── metrics.json        # 指标值及分子/分母
├── failures.jsonl      # 失败类型和相关 run_id
└── report.md           # 人类可读对照报告
```

指标口径：

- Hit@1：Top-1 `root_cause_type` 等于标签的 case 数 / 有根因标签的 case 数；
- Hit@3：Top-3 包含标签的 case 数 / 有根因标签的 case 数；
- Evidence Recall：命中的期望证据类型数 / 期望证据类型总数，逐 case 后 macro average；
- Tool Success Rate：成功 ToolExecution 数 / attempted ToolExecution 数；
- E2E Success Rate：产生合法终态报告的 case 数 / 全部 case 数；
- Recovery Success Rate：注入 runtime failure 后最终成功且报告合法的 run 数 / 注入 run 数；
- P95 Latency：同一运行口径下端到端耗时的 95 分位；
- Normal/Noise case 另报 False Positive Rate，不能被排除在平均值之外。

方案至少比较：现有 DeepRCA baseline 与 OpsPilot hybrid。LLM-only 可作为附加实验，但不作为完成 Runtime 的前置条件。

## 9. API 目标

```text
POST /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/result
GET  /api/v1/runs/{run_id}/events
POST /api/v1/runs/{run_id}/retry
WS   /api/v1/runs/{run_id}/stream

POST /api/v1/evaluations
GET  /api/v1/evaluations/{evaluation_id}
GET  /api/v1/evaluations/{evaluation_id}/report
```

迁移期保留 `/api/v1/analyze`，内部转调 Create Run；新旧接口的同一告警结果需要 contract test。Cancel、Replay 等接口只有在三阶段核心验收全部完成后再评估。

## 10. 关键架构决定

| 决定 | 选择 | 原因 | 暂不采用 |
|---|---|---|---|
| Agent 数量 | Coordinator + Root Cause | 重点是工具使用与恢复，不是 Agent 数量 | 独立 DB/Redis/Kafka/RPC Agent |
| 持久状态 | PostgreSQL | Run/Step/Checkpoint 需要事务、约束和可查询历史 | Redis 作为唯一状态源 |
| 任务传递 | 简单 Redis Queue | 足够验证 API/Worker 解耦和恢复 | 首版即上 Streams/Lease/DLQ |
| 交付语义 | 至少一次 + 幂等 | Worker 可在成功后、确认前退出 | 宣称 exactly-once |
| Checkpoint 粒度 | 稳定 Step 边界 | 容易测试、解释和版本化 | 保存任意函数内部状态 |
| RCA | 确定性筛选/规则 + LLM 总结 | 降低幻觉，保留可解释证据 | 原始监控数据直接交给 LLM |
| Benchmark | 固定数据版本和 split | 支持同集比较与真实回归 | 手工挑选 demo case |
| 部署 | Docker Compose | 足够展示多进程与依赖 | K8s |

## 11. 三阶段迁移边界

1. 场景与核心契约：冻结 schema、把领域 Expert 收敛为 Tool、建立可运行 baseline Benchmark；此时仍可在进程内执行。
2. 可恢复 Runtime：接入 PostgreSQL、Redis Queue、Worker、Checkpoint、Retry 和 Idempotency；新 Run API 成为主入口。
3. 故障恢复与证据闭环：完成故障注入、恢复测试、完整 Benchmark/Regression、Compose 和演示工件，并清理迁移兼容层。

每阶段都必须保持一条可运行纵向链路，不允许先批量创建空接口，最后才联调。

