# OpsPilot

[![OpsPilot Validation](https://github.com/1hao1hao/OpsPilot/actions/workflows/smoke-test.yml/badge.svg)](https://github.com/1hao1hao/OpsPilot/actions/workflows/smoke-test.yml)

OpsPilot 是一个面向微服务故障诊断的可恢复 Agent RCA 平台。系统接收服务告警，按版本化分析计划调用 Metrics、Logs、Changes、Trace、Topology 及领域工具，将异构观测转换为结构化 Evidence，再输出 Top-3 根因、置信度、证据链和处置建议。

项目重点解决两个问题：一是避免让 LLM 脱离监控事实直接猜测根因；二是让跨多个工具的长任务在 Worker 崩溃、工具超时和重复请求下仍能恢复并保持幂等。

## 核心能力

- Semantic Agent RCA：Coordinator / Planner 先生成变更、上游、下游、集群、错误日志、已知问题六维语义计划，再合并为统一的只读 Tool 执行计划。
- L2 Domain Experts：DB、Redis、Kafka、RPC Expert 消费已持久化 ToolResult 做领域下钻，不绕过 Runtime 重复访问外部系统。
- Deterministic + Rule + LLM：执行指标筛选、噪声过滤、WoW/DoD、IQR、波动检测和 R001–R008 专家规则；DeepSeek 可选且只能解释被证据约束的确定性候选。
- Recoverable Runtime：FastAPI、Redis `run_id` 队列、独立 Worker 和 PostgreSQL 事实源解耦任务提交与执行。
- Checkpoint & Idempotency：持久化 Run、Step、ToolExecution、Checkpoint、Report 和 RuntimeEvent，以 `request_id`、`tool_call_id` 防重。
- Evaluation & Regression：固定数据集、同集消融、逐 case prediction、失败工件、Runtime 故障注入和 CI 回归门禁。

## 已验证结果

### Runtime v2 六维主链回归

在不调用外部模型的 24-case dev 集上，恢复六维语义层、L2 Expert 和 L3 算法链后的结果为：

- Root Cause Hit@1 / Hit@3：20/20 / 20/20
- Evidence Recall：1.0
- False Positive Rate：0/4
- Tool Success：240/240
- Model API Calls：0

逐 case 结果和空失败集保存在 [`artifacts/evaluations/20260829T052113Z-opspilot_hybrid-dev/`](artifacts/evaluations/20260829T052113Z-opspilot_hybrid-dev/)。该结果是 dev 回归，不替代 frozen test。

### DeepSeek 三路 RCA 消融（Runtime v1 冻结基线）

固定 `opspilot-rca@1.0.0` frozen test 包含 12 个 case，其中 10 个 fault、2 个 normal/noise。三组均使用 `deepseek-v4-flash`、temperature 0、thinking disabled。

| 方案 | Hit@1 | Hit@3 | Evidence Recall | FPR | Tool Success | API Token | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| LLM Only | 4/10 | 4/10 | 0.0 | 0/2 | n/a | 4985 | 2200.236 ms |
| LLM + Tools | 10/10 | 10/10 | 0.0 | 1/2 | 108/108 | 6132 | 2400.149 ms |
| Tools + Deterministic Evidence + LLM | 10/10 | 10/10 | 1.0 | 0/2 | 108/108 | 5660 | 2489.135 ms |

LLM Only 只接收公开告警字段，不读取作为工具后端的隐藏观测；LLM + Tools 接收原始工具结果；Hybrid 进一步加入确定性 Evidence 和候选约束。Hybrid 相比 LLM + Tools 保持 10/10 Hit@1，同时消除 normal case 误报，并减少 7.7% Token。

### Runtime Reliability（Runtime v1 冻结基线）

在真实 PostgreSQL、Redis 和独立 Worker 子进程上运行 5 类故障 × 3 轮：

- Recovery Success：15/15
- E2E Success：15/15
- 重复成功 ToolExecution：0/132
- Worker Crash P95 恢复延迟：602.711 ms
- 全部 trial P95 E2E：1571.484 ms
- 故障类型：WorkerCrash、ToolTimeout、ToolHTTP500、DuplicateRequest、DuplicateDelivery

### Sequential vs Parallel

同一 24-case dev workload、每个工具固定 20 ms 异步 I/O、每种模式运行 3 轮：

| 模式 | Runs | P50 | P95 | Tool Success |
|---|---:|---:|---:|---:|
| Sequential | 72 | 201.909 ms | 202.531 ms | 720/720 |
| Parallel | 72 | 20.819 ms | 21.135 ms | 720/720 |

Runtime v2 受控实验中 Parallel 的 P95 加速为 9.583×。该结果用于验证 10 个统一工具的异步调度行为，不代表生产网络 SLA；完整运行记录位于 [`artifacts/evaluations/20260829T052345Z-tool-concurrency-v1/`](artifacts/evaluations/20260829T052345Z-tool-concurrency-v1/)。

Runtime v1 数字可追溯到 [`artifacts/stage3/final_evidence.json`](artifacts/stage3/final_evidence.json)。v2 的 dev 与并发结果分别链接到对应新工件；v2 恢复六维、L2 Expert 与完整算法链后，仍需重新运行 frozen test 和故障矩阵才能声明新的正式数字。

## 架构

```mermaid
flowchart TB
    A[Alert] --> B[FastAPI Run API]
    B --> C[Agent Runtime]
    C --> D[Six-Dimension Planner]
    D --> E[Tool Registry / Executor]
    E --> L1[L1 Dimension Analyzers]
    E --> L2[L2 DB / Redis / Kafka / RPC Experts]
    L1 --> F[Evidence Collector]
    L2 --> F
    F --> G[Filter / Noise / WoW-DoD / IQR / Volatility]
    G --> H[Expert Rules + Root Cause Ranking]
    H --> LLM[Optional Constrained DeepSeek Explanation]
    LLM --> I[Diagnosis Report]

    B <--> PG[(PostgreSQL<br/>Run / Step / Checkpoint / Report)]
    C <--> R[(Redis run_id Queue)]
    R <--> W[Independent Worker]
    W --> C
    C --> T[RuntimeEvent Trace]
    FI[Fault Injection] --> W
    FI --> E
    T --> EV[Evaluation / Regression]
    I --> EV
```

PostgreSQL 是运行状态和结果的唯一事实源，Redis 只传递 `run_id`。Worker 每完成一个 Step，就在事务中保存输出和 Checkpoint；恢复扫描发现 stale `RUNNING` Run 后，将其重新入队，新 Worker 从最近成功 Checkpoint 后继续。

Jaeger、OpenTelemetry 或 Mock Trace 保持各自外部格式，由 Trace Adapter 转成内部统一 Span；系统再利用 `span_id / parent_span_id` 重建调用树，逐 Span 检测异常并保留根服务到异常服务的完整路径。

在线 Root Cause Agent 默认使用无外部依赖的确定性摘要器。仅显式设置 `OPSPILOT_LLM_ENABLED=true` 时才调用 DeepSeek，并将候选范围限制为确定性算法已经选出的 Top-1；自动测试不会访问付费 API。

## 请求主链

```text
Alert
-> POST /api/v1/runs
-> PostgreSQL 创建 Run(QUEUED)
-> Redis enqueue(run_id)
-> Worker 领取 Run
-> Planner 生成六维语义计划并合并只读 AnalysisPlan
-> Metrics / Logs / Changes / Trace / Domain Tools
-> L1 六维分析 + L2 DB / Redis / Kafka / RPC Expert
-> Evidence 标准化与去重
-> 指标筛选 / NoiseFilter / WoW-DoD / IQR / 波动检测 / 专家规则
-> Root Cause Agent（确定性排序 + 可选受约束 LLM 解释）
-> DiagnosisReport + RuntimeEvent
-> Run(SUCCEEDED)
```

故障恢复链：

```text
Worker Crash
-> stale Run scan
-> RUNNING -> RETRYING -> QUEUED
-> load latest Checkpoint
-> skip completed Steps
-> resume unfinished Step
-> SUCCEEDED
```

## 技术栈

- Python 3.11、FastAPI、Pydantic
- PostgreSQL、SQLAlchemy、Alembic
- Redis Queue、独立异步 Worker
- asyncio、httpx
- DeepSeek Chat Completion API（离线 Evaluation；在线需显式启用）
- Docker Compose、GitHub Actions
- pytest、Ruff

## 快速开始

### 本地 Python 环境

```bash
bash scripts/bootstrap_dev_env.sh
source .venv/bin/activate
ruff check src tests
pytest -q --ignore=tests/smoke
```

### Docker Compose

```bash
docker compose --profile full up --build -d
```

首次启动会执行 Alembic migration，并启动 PostgreSQL、Redis、API、Worker 与 Mock Environment。

提交一个异步 RCA Run：

```bash
curl -X POST http://localhost:8000/api/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "demo-db-001",
    "alert": {
      "alert_id": "alert-db-001",
      "service_name": "checkout-service",
      "alert_type": "timeout",
      "severity": "P1",
      "timestamp": "2026-08-12T00:00:00Z",
      "signals": {"db": {"replication_lag_seconds": 20}}
    }
  }'
```

使用返回的 `run_id` 查询状态、结果与事件：

```bash
curl http://localhost:8000/api/v1/runs/<run_id>
curl http://localhost:8000/api/v1/runs/<run_id>/result
curl http://localhost:8000/api/v1/runs/<run_id>/events
```

## 可复现实验

### Runtime 故障注入

需要隔离的 PostgreSQL 和 Redis 测试实例：

```bash
export OPSPILOT_TEST_DATABASE_URL='postgresql+asyncpg://opspilot:opspilot@localhost:5432/opspilot'
export OPSPILOT_TEST_REDIS_URL='redis://localhost:6379/0'
python -m opspilot.evaluation.cli reliability --config benchmarks/configs/runtime_faults.yaml
```

### DeepSeek 三路消融

在项目根目录的 `.env` 中配置 `DEEPSEEK_API_KEY`。以下命令会真实访问付费 API；密钥不会写入实验工件。

```bash
python -m opspilot.evaluation.cli run --config benchmarks/configs/deepseek_llm_only.yaml --split test
python -m opspilot.evaluation.cli run --config benchmarks/configs/deepseek_tools.yaml --split test
python -m opspilot.evaluation.cli run --config benchmarks/configs/deepseek_hybrid.yaml --split test
```

### 串并行对照

```bash
python -m opspilot.evaluation.cli concurrency --config benchmarks/configs/tool_concurrency.yaml
```

每次评测都会生成 manifest、metrics、prediction/run 明细、failure 和人类可读报告。正式工件不会通过删除失败 case 或修改分母获得更好结果。

## 项目结构

```text
src/opspilot/
├── api/             # Run API、状态、结果、事件与 WebSocket
├── agents/          # 六维 Planner、L1 分析、L2 Expert 与 Root Cause Agent
├── evidence/        # Evidence 标准化、去重和排序
├── evaluation/      # RCA、可靠性、并发评测与报告
├── graph/           # 在线工作流和执行模式
├── persistence/     # PostgreSQL models 与 repositories
├── runtime/         # Worker、Queue、Checkpoint、Recovery、Idempotency
├── rca/             # 确定性算法链与专家规则
├── tracing/         # Trace Adapter、统一 Span 模型与调用树异常路径
├── llm/             # 可选 DeepSeek 证据约束解释
└── tools/           # Registry、Executor 与只读领域工具

benchmarks/
├── configs/         # baseline、DeepSeek、runtime、concurrency 配置
└── datasets/rca/v1/ # 固定 dev / frozen-test 数据

tests/
├── contract/
├── evaluation/
├── integration/
├── recovery/
├── regression/
└── smoke/
```

## 安全边界与限制

- 所有领域工具默认为只读；系统只生成诊断和处置建议，不自动修改生产资源。
- 当前数据集是固定合成故障场景，实验结果不能直接外推为生产准确率。
- DeepSeek 默认关闭；离线消融或在线显式启用时才访问 API，自动测试使用 fake transport。
- 并发实验使用固定工具 I/O 延迟，用于验证调度实现，不替代生产压测。
- 当前未实现 Kubernetes 生产部署、自动回滚、带副作用工具审批和 LLM Timeout Runtime 故障注入。
