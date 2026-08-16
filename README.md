# DeepRCA-Agent

[![OpsPilot Validation](https://github.com/1hao1hao/OpsPilot/actions/workflows/smoke-test.yml/badge.svg)](https://github.com/1hao1hao/OpsPilot/actions/workflows/smoke-test.yml)

LLM Agent 驱动的故障根因分析（Root Cause Analysis）智能体系统。采用 LangGraph 状态机驱动的多 Agent 协同架构，实现从告警接入到根因定位的全自动化故障诊断闭环。

> **OpsPilot 重构状态（Stage 3 实现与实验完成）**：DeepRCA 原有告警接入、确定性 RCA 算法和 Mock 场景被保留为兼容能力；OpsPilot 新增统一契约与 Benchmark、PostgreSQL 事实源、Redis `run_id` 队列、独立 Worker、Step Checkpoint、工具幂等和可重复故障评测。所有项目数字都来自 `artifacts/evaluations/`，最终索引见 [`artifacts/stage3/final_evidence.json`](artifacts/stage3/final_evidence.json)。Docker Compose 的权威验证在 GitHub Actions 全新 runner 上执行，校园开发服务器不需要安装 Docker daemon。

## 已验证结果

固定 `rca-benchmark@1.0.0` frozen test 共 12 个 case；DeepRCA baseline 与 OpsPilot hybrid 使用同一数据、split 和指标公式。恢复评测使用真实 PostgreSQL、Redis 和独立 Worker 子进程，覆盖 WorkerCrash、ToolTimeout、ToolHTTP500、DuplicateRequest、DuplicateDelivery 五类故障，每类 3 轮。具体数值和 evaluation ID 只引用最终证据索引，不在此手工复制不可追溯结果。

## 验证策略

- 每次 push 和 pull request 由 `.github/workflows/smoke-test.yml` 自动执行 Python 单元测试；
- Docker job 在全新 GitHub runner 构建镜像、执行 Alembic migration，并启动 PostgreSQL、Redis、API、Worker 与 Mock 环境；
- 持久化 API smoke 会真实提交 Run，验证独立 Worker 完成任务并从 PostgreSQL 返回 RCA 报告；
- Stage 3 Demo job 在隔离 PostgreSQL/Redis 上运行 15-trial Worker Crash Recovery，并对同一 dev split 执行 baseline/hybrid Evaluation；
- 自动验证不访问付费 LLM；Compose 日志无论成功或失败都会作为 Actions artifact 上传；
- 校园服务器只需要项目级 `.venv` 做开发和非容器测试，本地 Docker 属于可选能力。

最新结果以仓库的 [GitHub Actions](https://github.com/1hao1hao/OpsPilot/actions/workflows/smoke-test.yml) 为准。

## 四个可复现 Demo

以下命令不访问付费 LLM。首次启动会执行 Alembic migration，`api` 只提交任务，`worker` 独立执行。

### 1. 正常 RCA

```bash
docker compose --profile full up --build -d
curl -X POST http://localhost:8000/api/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-normal-1","alert":{"alert_id":"demo-db","service_name":"checkout-service","alert_type":"timeout","severity":"P1","timestamp":"2026-08-12T00:00:00Z","signals":{"db":{"replication_lag_seconds":20}}}}'
# 使用响应中的 run_id：
curl http://localhost:8000/api/v1/runs/<run_id>/result
```

### 2. 异步 Worker

```bash
docker compose --profile full stop worker
# 再次 POST /api/v1/runs 后，GET /api/v1/runs/<run_id> 返回 QUEUED
docker compose --profile full start worker
# 同一个 run_id 最终进入 SUCCEEDED
```

### 3. Worker Crash Recovery

```bash
# 需要隔离的 PostgreSQL/Redis；变量明确指向测试服务，禁止指向生产库。
export OPSPILOT_TEST_DATABASE_URL='postgresql+asyncpg://opspilot:opspilot@localhost:5432/opspilot'
export OPSPILOT_TEST_REDIS_URL='redis://localhost:6379/0'
python -m opspilot.evaluation.cli reliability --config benchmarks/configs/runtime_faults.yaml
```

输出目录包含 `reliability.json`、`trials.jsonl`、`failures.jsonl` 和 15 条逐 trial RuntimeEvent 时间线。WorkerCrash trial 的进程退出码序列为 `[97, 0, 0]`。

### 4. Evaluation

```bash
python -m opspilot.evaluation.cli run --config benchmarks/configs/deeprca_baseline.yaml --split dev
python -m opspilot.evaluation.cli run --config benchmarks/configs/opspilot_hybrid.yaml --split dev
```

每个目录都包含 manifest、predictions、metrics、failures 和 report；失败 case 不会从分母中删除。CI 只重复运行 dev 回归；最终 frozen-test 工件保持不可变，不在每次 push 中重复消费。

## 项目背景

本系统将传统人工排障流程自动化：接收告警后，自动执行六维度分析（变更/上游流量/下游依赖/集群状态/ErrorLog/已知问题），通过三层 Agent 协同推理定位根因，并输出可执行的修复建议。

核心设计参考：
- 美团 AIOps 故障处理助手（多 Agent 协同推理、满意度收集闭环、多维指标筛选根因定位）
- [open-swe](https://github.com/langchain-ai/open-swe)（Deep Agents + Subagent + Middleware 编排模式）

## 架构概览

```
┌──────────────────────────────────────────────────────────────┐
│                     FastAPI Gateway                          │
│              REST API + WebSocket 实时推送                    │
├──────────────────────────────────────────────────────────────┤
│                  Coordinator Agent (L1)                      │
│    Intake → Planner → Dispatcher → Collector → RootCause     │
│                          → Reporter                          │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│ DB Expert│Redis Exp │Mafka Exp │ RPC Exp  │ Change / Log    │
│  (L2)    │  (L2)    │  (L2)    │  (L2)    │  (L1维度分析)    │
├──────────┴──────────┴──────────┴──────────┴─────────────────┤
│                 Root Cause Agent (L3)                        │
│  指标筛选 → 多维对比 → 异常检测 → 证据排序 → 规则匹配 → LLM   │
├──────────────────────────────────────────────────────────────┤
│                    Mock Environment                          │
│   K8s Sim | MySQL Sim | Redis Sim | Kafka Sim | Alert Sim   │
└──────────────────────────────────────────────────────────────┘
```

### 三层 Agent 架构

| 层级 | Agent | 职责 | 关键能力 |
|------|-------|------|----------|
| L1 | Coordinator Agent | 告警解析、任务规划、并发调度、报告生成 | 六维度规划、asyncio.gather 并发、证据池聚合 |
| L2 | Domain Expert (×4) | 领域专项分析（DB/Redis/Mafka/RPC） | 各领域专属工具集、LangGraph 子图、置信度评估 |
| L3 | Root Cause Agent | 根因定位与推理 | 四分位异常检测、多维对比、专家规则引擎、LLM 推理 |

> **说明**：Change（变更分析）和 ErrorLog（错误日志分析）由 L1 维度分析模块直接处理，不走 L2 领域专家子图。

### 核心算法

- **QuantileAnomalyDetector**：四分位 IQR 异常检测，替代大模型做异常判断，降低幻觉风险
- **VolatilityDetector**：滚动标准差波动性突变检测
- **MultiDimensionComparator**：周同比（WoW）+ 日环比（DoD）双重确认
- **MetricFilter + NoiseFilter**：多维指标筛选 + 低影响抖动过滤
- **ExpertRuleEngine**：8 条专家经验规则（R001-R008），支持 `set_root_cause` / `boost_confidence` 两种动作

### 技术栈

| 类别 | 技术选型 |
|------|----------|
| Agent 编排 | LangGraph (StateGraph, 子图嵌套, Annotated reducer) |
| LLM 框架 | LangChain (@tool, Prompt 模板, 记忆管理) |
| API 层 | FastAPI + WebSocket |
| 并发处理 | asyncio.gather + ThreadPoolExecutor |
| 数据存储 | Redis（标准 Redis，内存降级） |
| 配置管理 | pydantic-settings（.env 文件） |
| 验证环境 | Docker Compose（K8s/MySQL/Redis/Kafka/微服务模拟器） |

## PRD 文档索引

| 文档 | 说明 |
|------|------|
| [01_overview_prd.md](prds/01_overview_prd.md) | 总体架构、技术选型、状态设计、项目目录结构、里程碑 |
| [02_general_analyzer_prd.md](prds/02_general_analyzer_prd.md) | 通用分析 Agent：六节点工作流、工具接口、验证 API |
| [03_domain_expert_prd.md](prds/03_domain_expert_prd.md) | 领域专家子 Agent：DB/Redis/Mafka/RPC |
| [04_root_cause_prd.md](prds/04_root_cause_prd.md) | 根因定位 Agent：异常检测算法、专家规则引擎、LLM 推理 |
| [05_mock_env_prd.md](prds/05_mock_env_prd.md) | 验证接口与模拟环境：K8s/中间件/微服务模拟器 |
| [06_containerization_prd.md](prds/06_containerization_prd.md) | 容器化部署与冒烟测试：Dockerfile、Compose Profile、冒烟测试工作流 |

## 快速启动

### 环境要求

- Python 3.11+
- Docker & Docker Compose
- Redis 7+

### 安装

```bash
# 克隆仓库
git clone https://github.com/HeafeyM/DeepRCA-Agent.git
cd DeepRCA-Agent

# 为本项目创建隔离环境（推荐，不向 Conda base 或 ~/.local 写依赖）
bash scripts/bootstrap_dev_env.sh
source .venv/bin/activate
```

`.venv/` 已被 `.gitignore` 排除。验证当前解释器应指向项目目录：

```bash
python -c 'import sys; print(sys.executable)'
# .../DeepRCA-Agent-master/.venv/bin/python
```

### 可选：Rocky/RHEL 共享主机上的 Docker

日常验证默认使用 GitHub Actions，不要求校园服务器安装 Docker。只有确实需要在 Rocky/RHEL 共享主机本地运行容器时，才考虑 Rootless Docker。安装前，管理员必须为当前账号在 `/etc/subuid` 和 `/etc/subgid` 各分配至少 65,536 个未占用 ID，并建议加载 `ip_tables`。管理员先确认区间未被占用，再执行（示例区间仅适用于空闲时）：

```bash
target_user='<项目账号>'
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$target_user"
sudo modprobe ip_tables
# 可选：允许注销后继续运行用户级 daemon
sudo loginctl enable-linger "$target_user"
```

管理员完成配置后执行：

```bash
bash scripts/install_rootless_docker.sh
export PATH="$HOME/bin:$PATH"
export DOCKER_HOST="unix:///run/user/$(id -u)/docker.sock"
docker info
docker-compose --profile smoke up --build --abort-on-container-exit
```

安装脚本在 `ip_tables` 尚未加载时会尝试官方安装器的无 iptables 模式，但必须以 `docker info` 和 Compose smoke 为最终判据。不要在共享登录节点自行启动 rootful `dockerd`。

### 配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，配置以下变量：
# LLM_API_KEY=your-api-key
# LLM_MODEL=your-model-name
# REDIS_HOST=localhost
```

### 启动服务

```bash
# 方式一：Docker Compose 一键启动（推荐）
# 完整环境（Redis + Agent + Mock Env）
docker compose --profile full up -d

# 冒烟测试（自动构建并执行，测试完毕后退出）
docker compose --profile smoke up --build --abort-on-container-exit

# 仅 Agent + Redis（对接外部 Mock）
docker compose --profile agent up -d

# 仅 Mock 环境（独立调试模拟器）
docker compose --profile mock up -d

# 仅 Redis（用于本地开发）
docker compose --profile redis-only up -d

# 方式二：本地开发模式
# 启动 Redis（如已通过 Docker 启动可跳过）
redis-server

# 终端 1：启动 Agent 服务
uvicorn opspilot.api.app:app --reload --port 8000

# 终端 2：启动 Mock 环境（注意入口文件为 mock_main.py）
uvicorn mock_main:app --reload --port 8001
```

### 验证

```bash
# 健康检查
curl http://localhost:8000/health

# 查看可用测试场景
curl http://localhost:8001/api/v1/mock/scenarios

# 提交故障分析请求
curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "alert_id": "alt-001",
    "service_name": "order-service",
    "alert_type": "timeout",
    "severity": "P1",
    "timestamp": "2026-07-13T10:00:00Z",
    "description": "接口超时",
    "labels": {"cluster": "prod-cluster", "env": "production", "app": "order"}
  }'

# 查询分析状态
curl http://localhost:8000/api/v1/analyze/{trace_id}/status

# 获取分析结果
curl http://localhost:8000/api/v1/analyze/{trace_id}/result
```

## API 端点

所有端点前缀 `/api/v1`：

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/analyze` | 提交故障分析请求（返回 202 + trace_id + websocket_url） |
| `GET` | `/analyze/{trace_id}/status` | 查询分析状态与进度 |
| `GET` | `/analyze/{trace_id}/result` | 获取分析结果 |
| `POST` | `/feedback` | 提交满意度反馈 |
| `WebSocket` | `/analyze/{trace_id}/stream` | 实时推送分析进度 |

Mock 环境端点前缀 `/api/v1/mock`，提供场景管理、模拟器查询、故障注入等接口。

## 项目结构

```
DeepRCA-Agent/
├── prds/                              # PRD 文档
│   ├── 01_overview_prd.md
│   ├── 02_general_analyzer_prd.md
│   ├── 03_domain_expert_prd.md
│   ├── 04_root_cause_prd.md
│   ├── 05_mock_env_prd.md
│   └── 06_containerization_prd.md
├── src/deeprca/                       # 核心代码
│   ├── agents/
│   │   ├── coordinator.py             # L1 Coordinator Agent（6 节点函数）
│   │   ├── root_cause.py             # L3 根因定位 Agent
│   │   └── dimensions/               # L1 六维度分析模块
│   │       ├── change.py             # 变更分析
│   │       ├── upstream.py           # 上游流量分析
│   │       ├── downstream.py         # 下游依赖分析
│   │       ├── cluster.py            # 集群状态分析
│   │       ├── errorlog.py           # 错误日志分析
│   │       └── problem.py            # 已知问题匹配
│   ├── graph/                        # LangGraph 图定义
│   │   ├── state.py                  # DeepRCAState（Annotated reducer）
│   │   ├── main_graph.py             # build_coordinator_graph()（6 节点 + 条件边）
│   │   └── subgraphs/                # L2 领域专家子图
│   │       ├── base_expert.py        # BaseExpertAgent 抽象基类
│   │       ├── db_expert.py          # DB 领域专家
│   │       ├── redis_expert.py       # Redis 领域专家
│   │       ├── mafka_expert.py       # Kafka 领域专家
│   │       ├── rpc_expert.py         # RPC 领域专家
│   │       ├── registry.py           # 专家注册表 + 并发调度
│   │       └── expert_mock_data.py   # Mock 数据
│   ├── detection/                    # 核心算法（确定性统计）
│   │   ├── quantile.py               # 四分位 IQR 异常检测
│   │   ├── volatility.py             # 滚动标准差波动性检测
│   │   ├── comparator.py             # 多维对比（WoW/DoD）
│   │   └── filters.py                # 指标筛选 + 噪声过滤
│   ├── models/                       # Pydantic 数据模型
│   │   ├── alert.py                  # AlertEvent, ParsedAlert
│   │   ├── evidence.py               # Evidence, EvidencePool, SubAgentResult
│   │   ├── feedback.py               # FeedbackRequest
│   │   ├── report.py                 # AnalysisReport
│   │   └── result.py                 # RootCauseResult, RootCauseCandidate
│   ├── tools/                        # LangChain @tool 工具集
│   │   ├── metrics.py                # query_metrics
│   │   ├── logs.py                   # query_error_logs
│   │   ├── changes.py                # query_recent_changes
│   │   ├── traces.py                 # query_trace
│   │   ├── topology.py               # query_topology
│   │   ├── alerts.py                 # query_related_alerts
│   │   └── mock_data.py              # Mock 数据生成
│   ├── mock_env/                     # Mock 模拟环境
│   │   ├── k8s_simulator.py          # K8s 集群模拟器
│   │   ├── mysql_simulator.py        # MySQL/DB 模拟器
│   │   ├── redis_simulator.py        # Redis 模拟器
│   │   ├── kafka_simulator.py        # Kafka 模拟器
│   │   ├── service_simulator.py      # 微服务调用链模拟器
│   │   ├── alert_simulator.py        # 预设场景 + 故障注入
│   │   └── mock_routes.py            # Mock API 路由
│   ├── config/                       # 配置模块
│   │   ├── settings.py               # pydantic-settings 配置类
│   │   └── __init__.py               # 导出 Settings, get_settings
│   ├── api/
│   │   ├── routes.py                 # REST API（5 端点）+ WebSocket
│   │   └── websocket.py              # WebSocket ConnectionManager
│   ├── middleware/                    # 中间件（预留）
│   │   └── __init__.py
│   ├── main.py                       # FastAPI 服务入口
│   └── __init__.py                   # 包版本号
├── mock_main.py                      # Mock 环境独立服务入口
├── tests/
│   ├── unit/                         # 单元测试
│   ├── integration/                  # 集成测试
│   ├── smoke/                        # 冒烟测试（Docker 容器内执行）
│   ├── guide/                        # 测试指南
│   └── Dockerfile                    # 冒烟测试容器镜像
├── Dockerfile                         # Agent 服务镜像
├── mock_env.Dockerfile                # Mock 环境镜像
├── docker-compose.yml                 # Profile: full/agent/mock/smoke/redis-only
├── pyproject.toml                     # 项目元数据 + 依赖声明
├── requirements.txt                   # 核心依赖
├── requirements-mock.txt              # Mock 环境依赖
├── requirements-dev.txt               # 开发依赖
├── .env.example                       # 环境变量模板
└── README.md
```

## 数据流

```
告警事件 (POST /api/v1/analyze)
  │
  ▼
intake (解析告警 → ParsedAlert)
  │
  ▼
planner (任务拆解 → 六维度分析计划)
  │
  ▼
dispatcher (并发调度 L2 领域专家)
  │  ├── DB Expert (子图: collect → analyze → conclude)
  │  ├── Redis Expert
  │  ├── Kafka Expert
  │  └── RPC Expert
  │  + L1 维度分析（change / errorlog / problem / upstream / downstream / cluster）
  │
  ▼
collector (汇聚所有线索 → EvidencePool)
  │
  ▼
root_cause (根因定位)
  │  ├── 多维指标筛选（高 QPS / 高失败率 / TP99 突变）
  │  ├── 多维对比（周同比 / 日环比）
  │  ├── 噪声过滤（低影响抖动）
  │  ├── 融合调用链路 + 专家经验规则（R001-R008）
  │  └── LLM 推理 → 输出根因 + 置信度 + 证据链
  │
  ▼
reporter (生成报告 + WebSocket 推送完成事件)
  │
  ▼
[满意度反馈] POST /api/v1/feedback → Kafka（生产环境）/ 日志（Mock 模式）
```

## 开发里程碑

| 阶段 | 内容 | 周期 |
|------|------|------|
| M1 | 基础框架搭建：LangGraph 图定义、状态模型、API 骨架 | 第 1 周 |
| M2 | L1 通用分析 Agent：六节点工作流、工具接口 | 第 2-3 周 |
| M3 | L2 领域专家 Agent：DB/Redis/Mafka/RPC | 第 4-5 周 |
| M4 | L3 根因定位 Agent：异常检测 + 规则引擎 + LLM 推理 | 第 6-7 周 |
| M5 | 模拟环境：K8s/中间件/微服务模拟器 | 第 8-9 周 |
| M6 | 端到端集成：预设场景验证 | 第 10 周 |
| M7 | 容器化部署 + 冒烟测试框架 | 第 11 周 |
| M8 | 满意度反馈闭环 + 性能优化 | 第 12 周 |

## 性能指标

| 指标 | 目标 |
|------|------|
| 端到端分析延迟 | ≤ 60s |
| 根因定位耗时 | ≤ 10s |
| 根因命中率 | ≥ 50% |
| 关键线索命中率 | ≥ 75% |
| 并发分析能力 | 6 维度并行 |

## License

MIT
