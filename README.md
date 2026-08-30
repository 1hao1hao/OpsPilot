# OpsPilot

[![OpsPilot Validation](https://github.com/1hao1hao/OpsPilot/actions/workflows/smoke-test.yml/badge.svg)](https://github.com/1hao1hao/OpsPilot/actions/workflows/smoke-test.yml)

OpsPilot 是一个面向微服务故障诊断的可恢复 Adaptive Agent RCA 平台。系统从少量低成本观测开始，根据本轮 Finding 和 Evidence 动态决定是否继续调用 Tool、下钻 Domain Expert 或结束调查，最终由确定性算法输出 Top-3 根因、证据链和处置建议。

项目避免让 LLM 脱离监控事实直接猜测根因；同时通过持久化 Checkpoint、稳定 ToolCall 幂等键和独立 Worker，使多轮调查能在进程崩溃、工具超时和重复投递后继续执行。

## 核心能力

- Adaptive Investigation：Seed Planner 按 `alert_type / severity / labels` 选择 2–3 个低成本 Tool；Controller 执行 `Plan → Execute → Observe → Re-plan`，动作限定为 `inspect_tool / invoke_expert / finalize`。
- 六维语义与动态 L2：保留 Change、Upstream、Downstream、Cluster、Errorlog、Problem 六维 L1；只有 Observation 命中领域后，才按需调度 DB、Redis、Kafka、RPC Expert 及专用 Tool。
- Evidence Gate：使用 Top-1 confidence、Top1/Top2 margin 和独立来源数判断是否结束；默认最多 4 轮、8 次 Tool、2 次 Expert，并拒绝重复 Action。
- Deterministic RCA：保留 MetricFilter、NoiseFilter、WoW/DoD、IQR、Volatility 和 R001–R008 ExpertRuleEngine；可选 DeepSeek 只解释确定性候选，不能覆盖 Evidence 或排名。
- Trace Path Analysis：适配 Mock、Jaeger 和 OTLP 格式，利用 `span_id / parent_span_id` 重建完整调用树，逐 Span 检测异常并保留根服务到异常服务的路径。
- Recoverable Runtime：FastAPI、PostgreSQL 事实源、Redis `run_id` 队列和独立 Worker；持久化 Run、Step、ToolExecution、Checkpoint、RuntimeEvent 和 Report。
- Reproducible Evaluation：固定 dev / frozen-test 数据、四路 Planner/L2 消融、逐 case prediction、失败集、并发实验和 Runtime 故障矩阵。

## Adaptive 调查示例

```text
Round 1: timeout alert
  metrics.query + traces.query + changes.query
  -> Trace path: order-service/payment-service/mysql

Evidence Gate: 证据不足

Round 2: invoke_expert(db)
  db.replication + db.slowlog
  -> replication lag = 15s

Evidence Gate: 证据充分
  -> L3 deterministic fusion
  -> DB_REPLICATION_LAG
```

每个 `DiagnosisReport.investigation` 和 RuntimeEvent 都能看到 Action 的原因、轮次、已执行 Tool、已调用 Expert、Gate 判断、预算消耗和停止原因。

## 已验证结果

### 四路核心消融（25-case dev）

| 方案 | Hit@1 | Hit@3 | Evidence Recall | FPR | Avg Tools | Avg Experts | Avg Rounds |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fixed Planner：六维 + 4 Expert 全跑 | 21/21 | 21/21 | 1.0 | 0/4 | 10.00 | 4.00 | 1.00 |
| Adaptive Planner：不提升 L2 Finding | 20/21 | 21/21 | 1.0 | 0/4 | 5.24 | 1.12 | 3.24 |
| Adaptive without Dynamic L2 | 4/21 | 4/21 | 0.190 | 0/4 | 2.76 | 0 | 1.00 |
| Full Adaptive RCA | 21/21 | 21/21 | 1.0 | 0/4 | 4.40 | 0.96 | 2.32 |

Full Adaptive 与 Fixed Planner 准确率相同，平均 Tool Calls 减少 56%，Expert Calls 减少 76%；Planner Action Valid Rate 为 1.0，Duplicate Action Rate 为 0。工件位于：

- [`Fixed Planner`](artifacts/evaluations/20260830T102439Z-opspilot_fixed_planner-dev/)
- [`Adaptive Planner`](artifacts/evaluations/20260830T102440Z-opspilot_adaptive_planner-dev/)
- [`Without Dynamic L2`](artifacts/evaluations/20260830T102441Z-opspilot_adaptive_without_dynamic_l2-dev/)
- [`Full Adaptive RCA`](artifacts/evaluations/20260830T102441Z-opspilot_full_adaptive-dev/)

未参与调参的 12-case frozen test 也达到 Hit@1 10/10、Hit@3 10/10、Evidence Recall 1.0、FPR 0/2，平均 Tool / Expert Calls 为 4.25 / 0.917；工件见 [`Full Adaptive frozen test`](artifacts/evaluations/20260830T103157Z-opspilot_full_adaptive-test/)。

这些数字来自固定合成数据集，仅验证当前实现和实验假设，不代表生产 SLA。Frozen test、Runtime v3 故障矩阵和并发实验由 CI 使用相同命令持续验证。

## 架构

```mermaid
flowchart TB
    A[Alert] --> API[FastAPI Run API]
    API --> SP[Seed Planner]
    SP --> TE[Tool Registry / Executor]
    TE --> L1[Six-Dimension L1 Analysis]
    L1 --> EP[Evidence Pool]
    EP --> G{Evidence Gate}
    G -->|insufficient| AP[Adaptive Planner]
    AP -->|inspect_tool| TE
    AP -->|invoke_expert| L2[DB / Redis / Kafka / RPC Expert]
    L2 --> DT[Dynamic Domain Tools] --> EP
    G -->|sufficient or budget exhausted| L3[Filter / Noise / WoW-DoD / IQR / Volatility / Rules]
    L3 --> RC[Deterministic Root Cause Ranking]
    RC --> LLM[Optional constrained DeepSeek explanation]
    LLM --> R[Diagnosis Report]

    API <--> PG[(PostgreSQL facts + checkpoints)]
    API --> Q[(Redis run_id Queue)] --> W[Independent Worker]
    W --> SP
    W --> E[RuntimeEvent]
```

PostgreSQL 是状态和结果的唯一事实源，Redis 只传递 `run_id`。Worker 在 Action/Tool/Gate 边界保存调查上下文；恢复扫描将 stale Run 重新入队，新 Worker 从最近 Checkpoint 继续未完成 Action。

## 技术栈

Python 3.11、FastAPI、Pydantic、asyncio、PostgreSQL、SQLAlchemy、Alembic、Redis、httpx、Docker Compose、GitHub Actions、pytest、Ruff；DeepSeek 为可选解释模型。

## 快速开始

```bash
bash scripts/bootstrap_dev_env.sh
source .venv/bin/activate
ruff check src tests
pytest -q --ignore=tests/smoke
```

完整服务：

```bash
docker compose --profile full up --build -d
```

首次启动会运行 Alembic migration，并启动 PostgreSQL、Redis、API、Worker 和 Mock Environment。

## 可复现实验

四路自适应 RCA 消融：

```bash
python -m opspilot.evaluation.cli run --config benchmarks/configs/fixed_planner.yaml --split dev
python -m opspilot.evaluation.cli run --config benchmarks/configs/adaptive_planner.yaml --split dev
python -m opspilot.evaluation.cli run --config benchmarks/configs/adaptive_without_dynamic_l2.yaml --split dev
python -m opspilot.evaluation.cli run --config benchmarks/configs/full_adaptive_rca.yaml --split dev
python -m opspilot.evaluation.cli run --config benchmarks/configs/full_adaptive_rca.yaml --split test
```

Runtime 故障矩阵：

```bash
export OPSPILOT_TEST_DATABASE_URL='postgresql+asyncpg://opspilot:opspilot@localhost:5432/opspilot'
export OPSPILOT_TEST_REDIS_URL='redis://localhost:6379/0'
python -m opspilot.evaluation.cli reliability --config benchmarks/configs/runtime_faults.yaml
```

串并行对照：

```bash
python -m opspilot.evaluation.cli concurrency --config benchmarks/configs/tool_concurrency.yaml
```

每次评测生成 manifest、metrics、predictions、failures 和报告；失败 case 保留在分母中。

## 项目结构

```text
src/opspilot/
├── agents/          # Seed Planner、L1/L2 与 Root Cause Agent
├── investigation/   # Adaptive Planner、Evidence Gate、Controller
├── tools/           # Registry、Executor、通用与领域 Tool
├── evidence/        # Finding/Evidence 标准化与去重
├── rca/             # 确定性算法与专家规则
├── tracing/         # Trace Adapter、Span Tree 和完整异常路径
├── runtime/         # Worker、Checkpoint、Recovery、Idempotency
├── persistence/     # PostgreSQL models 与 repositories
├── api/             # Run、状态、结果、事件与 WebSocket
└── evaluation/      # Dataset、消融、可靠性与报告

benchmarks/
├── configs/
└── datasets/rca/v1/
```

更完整的 Tool、触发条件、Finding 和 L1/L2 调用图见 [`docs/tools_intro.md`](docs/tools_intro.md)。设计需求见 [`docs/plan.md`](docs/plan.md)。

## 安全边界

- 所有领域 Tool 默认为只读；系统只输出诊断和建议，不自动修改生产资源。
- DeepSeek 默认关闭；只有显式设置 `OPSPILOT_LLM_ENABLED=true` 才用于受约束解释。
- 当前未实现 Kubernetes 生产部署、自动回滚或带副作用 Tool 的审批流程。
