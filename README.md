# OpsPilot

[![OpsPilot Validation](https://github.com/1hao1hao/OpsPilot/actions/workflows/smoke-test.yml/badge.svg)](https://github.com/1hao1hao/OpsPilot/actions/workflows/smoke-test.yml)

OpsPilot 是一个面向微服务故障诊断的可恢复 Agent RCA 平台。系统接收服务告警，规划并调用 Metrics、Logs、Changes、Trace、Topology 及领域工具，将异构观测转换为结构化 Evidence，再输出 Top-3 根因、置信度、证据链和处置建议。

项目重点解决两个问题：一是避免让 LLM 脱离监控事实直接猜测根因；二是让跨多个工具的长任务在 Worker 崩溃、工具超时和重复请求下仍能恢复并保持幂等。

## 核心能力

- Agent RCA：Coordinator / Planner 生成分析计划，统一 Tool Registry 与 Executor 执行只读诊断工具。
- Evidence-driven Reasoning：确定性阈值、支持/反证关系和稳定 Evidence ID 约束根因候选，再由 LLM 生成解释。
- Recoverable Runtime：FastAPI、Redis `run_id` 队列、独立 Worker 和 PostgreSQL 事实源解耦任务提交与执行。
- Checkpoint & Idempotency：持久化 Run、Step、ToolExecution、Checkpoint、Report 和 RuntimeEvent，以 `request_id`、`tool_call_id` 防重。
- Evaluation & Regression：固定数据集、同集消融、逐 case prediction、失败工件、Runtime 故障注入和 CI 回归门禁。

## 已验证结果

### DeepSeek 三路 RCA 消融

固定 `opspilot-rca@1.0.0` frozen test 包含 12 个 case，其中 10 个 fault、2 个 normal/noise。三组均使用 `deepseek-v4-flash`、temperature 0、thinking disabled。

| 方案 | Hit@1 | Hit@3 | Evidence Recall | FPR | Tool Success | API Token | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| LLM Only | 4/10 | 4/10 | 0.0 | 0/2 | n/a | 4985 | 2200.236 ms |
| LLM + Tools | 10/10 | 10/10 | 0.0 | 1/2 | 108/108 | 6132 | 2400.149 ms |
| Tools + Deterministic Evidence + LLM | 10/10 | 10/10 | 1.0 | 0/2 | 108/108 | 5660 | 2489.135 ms |

LLM Only 只接收公开告警字段，不读取作为工具后端的隐藏观测；LLM + Tools 接收原始工具结果；Hybrid 进一步加入确定性 Evidence 和候选约束。Hybrid 相比 LLM + Tools 保持 10/10 Hit@1，同时消除 normal case 误报，并减少 7.7% Token。

### Runtime Reliability

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
| Sequential | 72 | 220.672 ms | 498.469 ms | 648/648 |
| Parallel | 72 | 20.869 ms | 99.827 ms | 648/648 |

受控实验中 Parallel 的 P95 加速为 4.993×。该结果用于验证异步调度行为，不代表生产网络 SLA。

所有数字均可追溯到 [`artifacts/stage3/final_evidence.json`](artifacts/stage3/final_evidence.json)，逐 case 和逐 trial 结果保存在 [`artifacts/evaluations/`](artifacts/evaluations/)。

## 架构

```mermaid
flowchart TB
    A[Alert] --> B[FastAPI Run API]
    B --> C[Agent Runtime]
    C --> D[Coordinator / Planner]
    D --> E[Tool Registry / Executor]
    E --> F[Evidence Collector]
    F --> G[Deterministic RCA]
    G --> H[Root Cause Agent]
    H --> I[Diagnosis Report]

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

## 请求主链

```text
Alert
-> POST /api/v1/runs
-> PostgreSQL 创建 Run(QUEUED)
-> Redis enqueue(run_id)
-> Worker 领取 Run
-> Planner 生成 AnalysisPlan
-> Metrics / Logs / Changes / Trace / Domain Tools
-> Evidence 标准化与去重
-> 确定性候选筛选
-> Root Cause Agent
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
- DeepSeek Chat Completion API
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
├── agents/          # Coordinator 与 Root Cause Agent
├── evidence/        # Evidence 标准化、去重和排序
├── evaluation/      # RCA、可靠性、并发评测与报告
├── graph/           # 在线工作流和执行模式
├── persistence/     # PostgreSQL models 与 repositories
├── runtime/         # Worker、Queue、Checkpoint、Recovery、Idempotency
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
- DeepSeek 消融属于离线 Evaluation；自动测试使用 fake transport，不访问付费 API。
- 并发实验使用固定工具 I/O 延迟，用于验证调度实现，不替代生产压测。
- 当前未实现 Kubernetes 生产部署、自动回滚、带副作用工具审批和 LLM Timeout Runtime 故障注入。
