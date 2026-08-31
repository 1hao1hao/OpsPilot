# OpsPilot

[![OpsPilot Validation](https://github.com/1hao1hao/OpsPilot/actions/workflows/smoke-test.yml/badge.svg)](https://github.com/1hao1hao/OpsPilot/actions/workflows/smoke-test.yml)

OpsPilot 是一个面向微服务故障诊断的可恢复 Adaptive Agent RCA 平台。系统从少量低成本观测开始，每轮将 ToolResult、六维 L1、动态 L2、确定性算法与专家规则统一汇入 Evidence Pool，再由 Evidence Gate 决定是否继续调查，最终输出 Top-3 根因、证据链和处置建议。

项目避免让 LLM 脱离监控事实直接猜测根因；同时通过持久化 Checkpoint、稳定 ToolCall 幂等键和独立 Worker，使多轮调查能在进程崩溃、工具超时和重复投递后继续执行。

## 核心能力

- Adaptive Investigation：Seed Planner 按 `alert_type / severity / labels` 选择 2–3 个通用 Tool；证据不足时由可选 DeepSeek Planner 或确定性 fallback 选择 `inspect_tool / invoke_expert`，停止权只属于 Gate、硬预算和“无合法动作”。
- 六维语义与动态 L2：保留 Change、Upstream、Downstream、Cluster、Errorlog、Problem 六维 L1；DB、Redis、Kafka、RPC Expert 根据公开告警上下文与已观测 Finding 动态选择自己的领域 Tool 子集。
- Unified Evidence Engine：每一轮只执行一次完整的 Raw/L1/L2 Evidence、MetricFilter、NoiseFilter、WoW/DoD、IQR、Volatility、ExpertRuleEngine 和根因排名；Gate 与最终 Report 复用该轮同一份结果。
- Evidence Gate：使用 Top-1 confidence、Top1/Top2 margin 和独立 Observation 来源数判断是否结束；同一 ToolResult 派生的 Raw、Finding、Algorithm、Rule Evidence 只计一个来源。
- 权限边界：Seed 与 Adaptive Planner 只能直接调用通用 Tool；领域 Tool 必须经对应 Expert，中央校验器在执行前拒绝越权动作；Planner 永远看不到作为 Tool backend snapshot 的 `alert.signals`。
- Deterministic RCA：确定性 Evidence 与排名始终是最终事实；DeepSeek 可用于受约束的下一步规划和结果解释，模型异常、超时或非法输出会自动回退，不能覆盖 Evidence 或排名。
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

Unified Evidence Engine:
  Raw + L1 + DB Expert Finding
  + IQR / Volatility / Rules
  -> provisional ranking = DB_REPLICATION_LAG

Evidence Gate: 证据充分
  -> 直接使用本轮 Evidence + Ranking 生成报告
```

每个 `DiagnosisReport.investigation` 和 RuntimeEvent 都能看到 Action 的原因、轮次、已执行 Tool、已调用 Expert、Gate 判断、预算消耗和停止原因。

## 已验证结果

### Unified Evidence v4

| 数据集 | Hit@1 | Hit@3 | Evidence Recall | FPR | Avg Tools | Avg Experts | Avg Rounds |
|---|---:|---:|---:|---:|---:|---:|---:|
| 25-case dev | 21/21 | 21/21 | 1.0 | 0/4 | 4.96 | 1.96 | 3.00 |
| 12-case frozen test | 10/10 | 10/10 | 1.0 | 0/2 | 4.83 | 2.00 | 3.08 |

工件位于 [`dev`](artifacts/evaluations/20260831T081531Z-opspilot_full_adaptive-dev/) 和 [`frozen test`](artifacts/evaluations/20260831T081532Z-opspilot_full_adaptive-test/)。这些数字来自固定合成数据集，仅验证当前实现和实验假设，不代表生产 SLA。严格的 Observation provenance 使单观测 case 不再通过派生 Evidence 虚增来源，因此当前 dev / frozen 的预算耗尽率分别为 96% / 100%；报告仍使用最后一轮完整确定性排名。

## 架构

```mermaid
flowchart TB
    A[Alert] --> API[FastAPI Run API]
    API --> SP[Seed Planner]
    SP --> TE[Tool Registry / Executor]
    TE --> L1[Selected Six-Dimension L1]
    TE --> L2[Selected DB / Redis / Kafka / RPC Expert]
    L2 --> DT[Dynamic Domain Tool subset] --> TE
    L1 --> UE[Unified Deterministic Evidence Engine]
    L2 --> UE
    TE --> UE
    UE --> ALG[Filter / Noise / WoW-DoD / IQR / Volatility / Rules]
    ALG --> EP[Complete Evidence Pool]
    EP --> RC[Provisional deterministic ranking]
    RC --> G{Evidence Gate}
    G -->|insufficient| AP[DeepSeek Planner or deterministic fallback]
    AP -->|inspect general Tool| TE
    AP -->|invoke Domain Expert| L2
    G -->|sufficient / budget / no legal action| LLM[Optional constrained DeepSeek explanation]
    LLM --> R[Diagnosis Report]

    API <--> PG[(PostgreSQL facts + checkpoints)]
    API --> Q[(Redis run_id Queue)] --> W[Independent Worker]
    W --> SP
    W --> E[RuntimeEvent]
```

PostgreSQL 是状态和结果的唯一事实源，Redis 只传递 `run_id`。Worker 在 Action/Tool/Gate 边界保存调查上下文；恢复扫描将 stale Run 重新入队，新 Worker 从最近 Checkpoint 继续未完成 Action。

## 技术栈

Python 3.11、FastAPI、Pydantic、asyncio、PostgreSQL、SQLAlchemy、Alembic、Redis、httpx、Docker Compose、GitHub Actions、pytest、Ruff；DeepSeek 为可选调查 Planner 与解释模型。

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
├── investigation/   # LLM/Fallback Planner、Unified Evidence Engine、Gate、Controller
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
- DeepSeek 默认关闭；只有显式设置 `OPSPILOT_LLM_ENABLED=true` 才用于受约束的调查规划与结果解释。
- 当前未实现 Kubernetes 生产部署、自动回滚或带副作用 Tool 的审批流程。
