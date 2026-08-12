# DeepRCA-Agent 全链路全流程测试指南

> **目标环境**: macOS (Apple Silicon / Intel), 仅预装 Docker Desktop, 无其他组件
> **同时适用**: Windows (PowerShell) — 文档中提供了对应命令
> **适用版本**: master 分支 (commit ≥ a2935cd)
> **预估时间**: 30–60 分钟（含镜像构建）

## 目录

1. [环境准备](#1-环境准备)
2. [LLM 接入配置](#2-llm-接入配置)
3. [Docker 部署](#3-docker-部署)
4. [模拟环境准备](#4-模拟环境准备)
5. [Agent 服务验证](#5-agent-服务验证)
6. [全流程模拟：从告警到根因](#6-全流程模拟从告警到根因)
7. [WebSocket 实时流验证](#7-websocket-实时流验证)
8. [自动化冒烟测试](#8-自动化冒烟测试)
9. [故障排查手册](#9-故障排查手册)
10. [附录：API 速查表](#附录api-速查表)

---

## 1. 环境准备

### 1.1 前置条件

| 组件 | 要求 | 验证命令 |
|------|------|----------|
| Docker Desktop | ≥ 4.20 (含 Docker Compose v2) | `docker --version` |
| Docker Compose | v2.x (Docker Desktop 内置) | `docker compose version` |
| 磁盘空间 | ≥ 3 GB (镜像 + 容器) | — |
| 内存 | ≥ 4 GB 分配给 Docker | Docker Desktop → Settings → Resources |
| 网络 | 能访问公网拉取 `python:3.11-slim` 和 `redis:7-alpine` | — |

> **注意**: 核心流程（构建、运行、测试）不要求 macOS 上安装 Python。但以下两类可选操作会用到宿主机 Python：
> - `docker exec -i deeprca-agent python -m json.tool` 用于美化 curl 返回的 JSON（也可直接阅读原始输出）
> - 第 7.2 节的 `ws_test.py` 脚本用于验证 WebSocket（需 `pip install websockets`）
>
> 所有 JSON 美化命令都通过 Docker 容器内 Python 执行，不依赖宿主机 Python。
>
> **本地开发（可选）**: 如需在宿主机直接运行 `pip install -e .` 安装项目，Python 版本必须 ≥ 3.11（与 `pyproject.toml` 中 `requires-python = ">=3.11"` 一致）。Docker 内使用 `python:3.11-slim` 不受此限制。

### 1.2 克隆项目

```bash
git clone <your-repo-url> DeepRCA-Agent
cd DeepRCA-Agent
git checkout master
```

### 1.3 创建 .env 文件

```bash
cp .env.example .env
```

编辑 `.env`，按 [第 2 节](#2-llm-接入配置) 配置 LLM 参数。

---

## 2. LLM 接入配置

DeepRCA-Agent 使用 OpenAI 兼容 API 接口（通过 `langchain-openai` 的 `ChatOpenAI`）。支持三种接入方式：

### 方式 A：使用 OpenAI 官方 API（推荐）

在 `.env` 中设置：

```env
LLM_API_BASE=https://api.openai.com/v1
LLM_API_KEY=sk-your-actual-api-key
LLM_MODEL=gpt-4o
```

### 方式 B：使用本地 Ollama（无需 API Key）

1. 在 macOS 上安装 Ollama（这需要额外安装 Ollama，但属于 LLM 接入需求）：

```bash
# 安装 Ollama
brew install ollama
# 拉取模型
ollama pull qwen2.5:7b
# 启动服务
ollama serve
```

2. 在 `.env` 中设置：

```env
LLM_API_BASE=http://host.docker.internal:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5:7b
```

> **关键**: Docker 容器内访问宿主机 Ollama 必须使用 `host.docker.internal`，不能用 `localhost`。

### 方式 C：无 LLM 模式（降级模式）

如果不配置有效 LLM，Agent 会在 LLM 调用失败时自动降级到规则引擎模式（`degraded_mode=True`），仅使用统计学算法和专家规则 R001–R008 进行根因定位，不调用 LLM。

```env
LLM_API_BASE=http://localhost:11434/v1
LLM_API_KEY=
LLM_MODEL=gpt-4o
```

> 降级模式仍可完成全流程，但根因分析的置信度可能略低，且不会生成自然语言推理链。

### 验证 LLM 配置

部署完成后（见第 3 节），可通过以下方式验证：

```bash
# 在容器内测试 LLM 连通性
docker exec deeprca-agent python -c "
from deeprca.config import get_settings
from langchain_openai import ChatOpenAI
s = get_settings()
llm = ChatOpenAI(base_url=s.llm_api_base, api_key=s.llm_api_key, model=s.llm_model)
print(llm.invoke('hello').content[:100])
"
```

---

## 3. Docker 部署

### 3.1 架构概览

```
┌─────────────────────────────────────────────────┐
│                Docker Network (deeprca-net)      │
│                                                  │
│  ┌──────────┐    ┌──────────────┐  ┌──────────┐ │
│  │  Redis   │◄───│ deeprca-agent│  │ mock-env │ │
│  │  :6379   │    │   :8000      │  │  :8001   │ │
│  └──────────┘    └──────┬───────┘  └──────────┘ │
│                         │                        │
│                  ┌──────┴───────┐                │
│                  │  smoke-test  │                │
│                  │ (run-once)   │                │
│                  └──────────────┘                │
└─────────────────────────────────────────────────┘
```

### 3.2 Docker Compose Profile 说明

| Profile | 启动服务 | 用途 |
|---------|----------|------|
| `redis-only` | redis | 仅启动缓存 |
| `agent` | redis + deeprca-agent | Agent + 缓存（无独立 Mock） |
| `mock` | mock-env | 仅模拟环境 |
| `full` | redis + deeprca-agent + mock-env | 完整全栈部署 |
| `smoke` | redis + deeprca-agent + mock-env + smoke-test | 全栈 + 自动化测试 |

### 3.3 全栈部署（推荐）

```bash
# 构建并启动全部服务
docker compose --profile full up -d --build
```

首次构建约需 5–10 分钟（拉取基础镜像 + pip install 依赖）。

### 3.4 查看服务状态

```bash
docker compose --profile full ps
```

预期输出：

```
NAME                IMAGE                    STATUS                    PORTS
deeprca-redis       redis:7-alpine           Up (healthy)              0.0.0.0:6379->6379/tcp
deeprca-agent       deeprca-agent            Up (healthy)              0.0.0.0:8000->8000/tcp
deeprca-mock-env    deeprca-mock-env         Up (healthy)              0.0.0.0:8001->8001/tcp
```

> **等待健康检查通过**: Agent 容器有 15s `start_period`，Mock 环境有 10s。全部 `healthy` 通常需要 30–40 秒。

### 3.5 查看日志

```bash
# Agent 服务日志
docker compose logs -f deeprca-agent

# Mock 环境日志
docker compose logs -f mock-env

# 全部服务日志
docker compose logs -f
```

### 3.6 停止和清理

```bash
# 停止所有服务
docker compose --profile full down

# 停止并删除数据卷（彻底清理）
docker compose --profile full down -v
```

---

## 4. 模拟环境准备

### 4.1 模拟环境架构

模拟环境（`mock-env` 容器）包含 6 个模拟器，提供完整的分布式系统仿真能力：

| 模拟器 | 端点前缀 | 模拟能力 |
|--------|----------|----------|
| K8s | `/api/v1/mock/k8s/` | 集群、Deployment、Pod、Event、故障注入 |
| MySQL | `/api/v1/mock/db/{instance}/` | 指标、慢日志、拓扑、主从延迟、连接池耗尽 |
| Redis | `/api/v1/mock/redis/{instance}/` | 指标、热 Key、内存压力、命中率下降 |
| Kafka | `/api/v1/mock/kafka/{cluster}/` | 消费积压、消费者离线、Rebalance 风暴 |
| 微服务 | `/api/v1/mock/service/{name}/` | 拓扑、指标、日志、调用链、超时注入 |
| 告警 | `/api/v1/mock/scenarios/` | 8 个预设故障场景 + 端到端验证 |

### 4.2 验证模拟环境健康

```bash
curl http://localhost:8001/api/v1/mock/health
```

预期响应：

```json
{
  "status": "healthy",
  "simulators": ["k8s", "db", "redis", "kafka", "service", "alert"]
}
```

### 4.3 查看可用场景

```bash
curl http://localhost:8001/api/v1/mock/scenarios | docker exec -i deeprca-agent python -m json.tool
```

返回 8 个预设场景列表：

| 场景名 | 描述 | 严重度 | 告警类型 |
|--------|------|--------|----------|
| `db_slave_delay_timeout` | DB 主从延迟超时 | P1 | timeout |
| `oom_restart` | OOM 重启 | P0 | error_rate |
| `kafka_consumer_lag` | Kafka 消费积压 | P1 | resource |
| `change_induced_failure` | 配置变更导致故障 | P1 | timeout |
| `redis_memory_pressure` | Redis 内存压力 | P2 | error_rate |
| `traffic_spike_saturation` | 流量突增资源饱和 | P1 | timeout |
| `rpc_circuit_breaker` | RPC 熔断触发 | P1 | error_rate |
| `multi_dimension_anomaly` | 多维度异常共振 | P0 | error_rate |

### 4.4 重置模拟环境

每个测试场景前建议重置：

```bash
curl -X POST http://localhost:8001/api/v1/mock/reset
```

---

## 5. Agent 服务验证

### 5.1 健康检查

```bash
curl http://localhost:8000/health | docker exec -i deeprca-agent python -m json.tool
```

预期响应（Mock 模式下两个检查都 healthy）：

```json
{
  "status": "healthy",
  "version": "0.3.0",
  "env": "production",
  "checks": {
    "redis": "healthy",
    "mock_env": "healthy"
  }
}
```

> 如果 `mock_env` 显示 `unhealthy`，检查 Agent 容器是否能访问 `mock-env:8001`（同 Docker 网络）。

### 5.2 验证 API 端点可达

```bash
# 验证 API 路由存在（应返回 400 缺少字段，而非 404）
curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{}'
```

预期响应（Pydantic 校验失败，返回 HTTP 400 + 错误详情）：

```json
{
  "message": "告警格式校验失败",
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "service_name"],
      "msg": "Field required",
      "input": {}
    },
    {
      "type": "missing",
      "loc": ["body", "alert_type"],
      "msg": "Field required",
      "input": {}
    }
  ]
}
```

> **说明**: `detail` 为数组，每个元素包含 `type`（错误类型）、`loc`（字段路径）、`msg`（错误消息）。`alert_type` 受 `AlertType` 枚举约束，非法值（如 `"abc"`）会返回 `value_error`；`severity` 受 `AlertSeverity` 枚举约束，非法值（如 `"P5"`）同样会被拒绝。

---

## 6. 全流程模拟：从告警到根因

### 6.1 全流程架构

```
用户触发场景 → Mock 环境注入故障 → 生成告警 → 提交到 Agent API
                                                        │
                                                        ▼
                                              ┌─────────────────┐
                                              │  LangGraph 图   │
                                              │                 │
                                              │  intake         │
                                              │    ↓            │
                                              │  planner        │
                                              │    ↓            │
                                              │  dispatcher     │
                                              │  (6维并行+L2)   │
                                              │    ↓            │
                                              │  [条件边]       │
                                              │  check_timeout  │
                                              │  ┌──────┴──────┐│
                                              │  │normal  timeout│
                                              │  ↓         ↓   │
                                              │ collector  root_cause│
                                              │ (证据汇聚)  (降级)│
                                              │  └──────┬──────┘│
                                              │         ↓       │
                                              │     root_cause   │
                                              │    (L3 根因定位) │
                                              │         ↓       │
                                              │     reporter     │
                                              └────────┬────────┘
                                                       │
                                                       ▼
                                              返回分析报告
                                              (含根因+建议+置信度)
```

> **架构说明**: LangGraph 图包含 6 个节点（intake → planner → dispatcher → collector → root_cause → reporter）。`check_timeout` 是 `dispatcher` 的条件路由函数（非图节点），超时跳过 collector 直达 root_cause。6 维 L1 并行收集发生在 dispatcher_node（通过 asyncio.gather 并发调用 6 个维度分析器），collector_node 仅做证据池汇聚。

### 6.2 方式一：一键端到端（推荐）

使用 Mock 环境内置的 `/scenarios/{name}/run` 端点，自动完成"注入 → 分析 → 验证"全流程：

```bash
# 执行 db_slave_delay_timeout 场景
curl -X POST "http://localhost:8001/api/v1/mock/scenarios/db_slave_delay_timeout/run" \
  --max-time 120 | docker exec -i deeprca-agent python -m json.tool
```

预期响应结构：

```json
{
  "scenario": "db_slave_delay_timeout",
  "status": "passed",
  "trace_id": "trace-a1b2c3d4e5f6",
  "actual_root_cause": "数据库主从延迟导致 order-service 查询超时",
  "expected_root_cause": "数据库主从延迟导致 order-service 查询超时",
  "root_cause_matched": true,
  "actual_confidence": 0.87,
  "expected_confidence_min": 0.85,
  "confidence_passed": true,
  "final_status": "completed"
}
```

### 6.3 方式二：手动分步执行

适合调试和理解每一步。

#### 步骤 1: 重置模拟环境

```bash
curl -X POST http://localhost:8001/api/v1/mock/reset
```

#### 步骤 2: 应用故障场景（注入故障）

```bash
curl -X POST http://localhost:8001/api/v1/mock/scenarios/oom_restart/apply \
  -H "Content-Type: application/json" | docker exec -i deeprca-agent python -m json.tool
```

#### 步骤 3: 获取告警事件

```bash
# 查看场景详情（含生成的告警事件）
curl http://localhost:8001/api/v1/mock/scenarios/oom_restart | docker exec -i deeprca-agent python -m json.tool
```

从响应中提取 `alert` 字段。

#### 步骤 4: 提交告警到 Agent

```bash
curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "alert_id": "alt-manual-001",
    "service_name": "order-service",
    "alert_type": "error_rate",
    "severity": "P0",
    "timestamp": "2026-07-22T10:00:00Z",
    "description": "order-service 错误率突增至 15%",
    "labels": {"cluster": "prod-cluster-01", "env": "production"}
  }' | docker exec -i deeprca-agent python -m json.tool
```

预期响应（202 Accepted）：

```json
{
  "trace_id": "trace-a1b2c3d4e5f6",
  "status": "running",
  "websocket_url": "ws://localhost:8000/api/v1/analyze/trace-a1b2c3d4e5f6/stream"
}
```

> **保存 `trace_id`**，后续查询需要用到。

#### 步骤 5: 轮询分析状态

```bash
TRACE_ID="trace-a1b2c3d4e5f6"  # 替换为实际 trace_id

curl http://localhost:8000/api/v1/analyze/$TRACE_ID/status | docker exec -i deeprca-agent python -m json.tool
```

分析过程中 `status` 为 `running`，完成后变为 `completed`。通常需要 5–30 秒。

```bash
# 循环轮询直到完成 (macOS/Linux bash)
while true; do
  STATUS=$(curl -s http://localhost:8000/api/v1/analyze/$TRACE_ID/status | docker exec -i deeprca-agent python -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "Status: $STATUS"
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ]; then
    break
  fi
  sleep 2
done
```

Windows PowerShell 版本:
```powershell
while ($true) {
  $resp = curl -s "http://localhost:8000/api/v1/analyze/$TRACE_ID/status"
  $status = ($resp | docker exec -i deeprca-agent python -c "import sys,json; print(json.load(sys.stdin)['status'])")
  Write-Host "Status: $status"
  if ($status -eq "completed" -or $status -eq "failed") { break }
  Start-Sleep 2
}
```

#### 步骤 6: 获取分析结果

```bash
curl http://localhost:8000/api/v1/analyze/$TRACE_ID/result | docker exec -i deeprca-agent python -m json.tool
```

预期响应结构：

```json
{
  "trace_id": "trace-a1b2c3d4e5f6",
  "status": "completed",
  "report": {
    "trace_id": "trace-a1b2c3d4e5f6",
    "summary": "...",
    "root_cause": "...",
    "confidence": 0.9,
    "key_evidence": [...],
    "suggestions": [...],
    "satisfaction_url": "..."
  },
  "root_cause": {
    "best_candidate": {
      "root_cause": "服务内存溢出导致 Pod 被 Kill 并重启",
      "confidence": 0.9,
      "evidence_chain": [...]
    },
"candidates": [...]
  }
}
```

#### 步骤 7: 提交反馈

反馈支持两种 token 传递方式:

**方式 A: 通过请求体提交（推荐）**

```bash
curl -X POST http://localhost:8000/api/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "'$TRACE_ID'",
    "feedback_token": "manual-test",
    "satisfaction": 5,
    "root_cause_correct": true,
    "comment": "分析准确，根因定位正确"
  }' | docker exec -i deeprca-agent python -m json.tool
```

**方式 B: 通过 URL query string 提交（satisfaction_url 生成的链接即使用此方式）**

```bash
curl -X POST "http://localhost:8000/api/v1/feedback?trace_id=$TRACE_ID&token=manual-test" \
  -H "Content-Type: application/json" \
  -d '{
    "satisfaction": 5,
    "root_cause_correct": true,
    "comment": "分析准确，根因定位正确"
  }' | docker exec -i deeprca-agent python -m json.tool
```

> **字段说明**: `satisfaction` 为必需字段（整数 1-5），`trace_id` 为必需字段。`root_cause_correct`、`comment`、`feedback_token` 为可选字段。

### 6.4 逐场景执行所有 8 个场景

```bash
SCENARIOS=(
  "db_slave_delay_timeout"
  "oom_restart"
  "kafka_consumer_lag"
  "change_induced_failure"
  "redis_memory_pressure"
  "traffic_spike_saturation"
  "rpc_circuit_breaker"
  "multi_dimension_anomaly"
)

for scenario in "${SCENARIOS[@]}"; do
  echo "=========================================="
  echo "Running scenario: $scenario"
  echo "=========================================="
  curl -X POST "http://localhost:8001/api/v1/mock/scenarios/$scenario/run" \
    --max-time 120 | docker exec -i deeprca-agent python -m json.tool
  echo ""
done
```

---

## 7. WebSocket 实时流验证

### 7.1 使用 websocat（需额外安装）

```bash
# 安装 websocat
brew install websocat

# 连接 WebSocket（替换 trace_id）
TRACE_ID="trace-a1b2c3d4e5f6"
websocat "ws://localhost:8000/api/v1/analyze/$TRACE_ID/stream"
```

> **注意**: 上述 URL 中 `localhost` 依赖 `APP_EXTERNAL_HOST` 环境变量配置。
> 默认值 `localhost` 适用于宿主机本地访问。如果从其他机器访问 Docker 宿主机，
> 需将 `APP_EXTERNAL_HOST` 设置为宿主机的实际 IP 或域名。

### 7.2 使用 Python 脚本验证

```python
# 保存为 ws_test.py，在宿主机执行（需 pip install websockets）
import asyncio
import json
import websockets

async def listen():
    trace_id = "trace-a1b2c3d4e5f6"  # 替换为实际 trace_id
    uri = f"ws://localhost:8000/api/v1/analyze/{trace_id}/stream"
    async with websockets.connect(uri) as ws:
        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            print(f"[{data.get('event')}] {json.dumps(data, ensure_ascii=False)}")
            if data.get("event") in ("completed", "error"):
                break

asyncio.run(listen())
```

### 7.3 预期 WebSocket 消息流

```
[connected] {"trace_id": "trace-xxx", "event": "connected"}
[status]    {"trace_id": "trace-xxx", "event": "status", "status": "running", "timestamp": "..."}
[status]    {"trace_id": "trace-xxx", "event": "status", "status": "running", "timestamp": "..."}
[completed] {"trace_id": "trace-xxx", "event": "completed", "status": "completed", "timestamp": "..."}
```

> **说明**: broadcast 推送的 completed 事件包含 `status` 和 `timestamp` 字段。轮询兜底的 completed 消息还额外包含 `report` 和 `root_cause` 完整内容。如需获取完整报告，请通过 `GET /api/v1/analyze/{trace_id}/result` 端点查询。

---

## 8. 自动化冒烟测试

### 8.1 一键冒烟测试（Docker Compose Smoke Profile）

```bash
# 启动全栈 + 自动运行冒烟测试
docker compose --profile smoke up --build --abort-on-container-exit
```

此命令会：
1. 构建 Redis、Agent、Mock 环境和 Smoke Test 容器
2. 等待 Agent 和 Mock 环境健康检查通过
3. 自动运行 `tests/smoke/` 目录下所有测试
4. 测试完成后自动退出

### 8.2 查看冒烟测试结果

```bash
# 查看 smoke-test 容器日志
docker compose logs smoke-test
```

### 8.3 手动在容器内运行测试

```bash
# 进入 Agent 容器运行单元测试
docker exec deeprca-agent python -m pytest tests/unit/ -v --tb=short

# 进入 Agent 容器运行冒烟测试（需 mock-env 也运行）
docker exec deeprca-agent python -m pytest tests/smoke/test_smoke.py -v --tb=short
```

### 8.4 测试矩阵说明

| 测试目录 | 测试数 | 依赖 | 说明 |
|----------|--------|------|------|
| `tests/unit/` | 167 | 无外部依赖 | 纯单元测试，内存 Mock；统计口径为 `def test_*` 函数数量 |
| `tests/smoke/test_smoke.py` | 17 | 无外部依赖 | 端到端验证，内存 Mock 驱动 LangGraph 图（不依赖外部 HTTP 服务，与 test_e2e_scenarios.py 区分） |
| `tests/smoke/test_agent_flow.py` | 5 | Agent + Mock 运行 | HTTP API 集成测试 |
| `tests/smoke/test_health.py` | 4 | Agent + Mock 运行 | 健康检查集成测试 |
| `tests/smoke/test_mock_sims.py` | 20 | Mock 运行 | Mock API 集成测试 |
| `tests/smoke/test_e2e_scenarios.py` | 8 (1 函数 × 8 参数化实例) | Agent + Mock 运行 | 8 场景端到端验证 |

---

## 9. 故障排查手册

### 9.1 Agent 容器启动失败

**症状**: `docker compose logs deeprca-agent` 显示 `ModuleNotFoundError` 或 `ImportError`

**原因**: 镜像构建时依赖安装不完整

**修复**:
```bash
docker compose --profile full down
docker compose --profile full build --no-cache deeprca-agent
docker compose --profile full up -d
```

### 9.2 健康检查显示 `unhealthy`

**症状**: `/health` 返回 `"status": "degraded"`, `redis` 或 `mock_env` 为 `unhealthy`

**排查**:
```bash
# 检查 Redis 连通性
docker exec deeprca-agent redis-cli -h redis ping

# 检查 Mock 环境连通性
docker exec deeprca-agent curl -s http://mock-env:8001/api/v1/mock/health
```

**常见原因**:
- Redis 未启动 → `docker compose --profile full up -d redis`
- Mock 环境未启动 → `docker compose --profile full up -d mock-env`
- 网络隔离 → 确认所有服务在同一 `deeprca-net` 网络

### 9.3 LLM 调用失败

**症状**: Agent 日志显示 `openai.APIConnectionError` 或根因分析置信度极低

**排查**:
```bash
# 在容器内测试 LLM 连通性
docker exec deeprca-agent python -c "
from deeprca.config import get_settings
s = get_settings()
print(f'LLM_API_BASE={s.llm_api_base}')
print(f'LLM_MODEL={s.llm_model}')
print(f'MOCK_ENV_ENABLED={s.mock_env_enabled}')
"
```

**常见原因**:
- 使用 Ollama 但未设置 `host.docker.internal` → 修改 `.env` 中的 `LLM_API_BASE`
- API Key 无效 → 检查 `LLM_API_KEY`
- 模型名不匹配 → 确认 `LLM_MODEL` 与实际可用模型一致

> **降级行为**: LLM 不可用时，Agent 自动降级到规则引擎模式，仍可完成分析。

### 9.4 场景执行超时

**症状**: `/scenarios/{name}/run` 请求超过 120 秒未返回

**排查**:
```bash
# 查看 Agent 日志是否有异常
docker compose logs --tail=50 deeprca-agent

# 检查 Agent 分析超时配置
docker exec deeprca-agent python -c "
from deeprca.config import get_settings
print(f'ANALYSIS_TIMEOUT={get_settings().analysis_timeout}s')
"
```

**修复**: 增大 `.env` 中的 `ANALYSIS_TIMEOUT`（默认 60 秒）。

### 9.5 Docker 端口冲突

**症状**: `docker compose up` 报 `port is already allocated`

**排查**:

macOS / Linux:
```bash
lsof -i :8000
lsof -i :8001
lsof -i :6379
```

Windows (PowerShell):
```powershell
netstat -ano | findstr :8000
netstat -ano | findstr :8001
netstat -ano | findstr :6379
```

**修复**: 停止占用端口的进程，或修改 `docker-compose.yml` 中的端口映射。

### 9.6 Apple Silicon 架构兼容性

**症状**: 镜像构建慢或出现 `platform mismatch` 警告

**说明**: `python:3.11-slim` 和 `redis:7-alpine` 都支持 ARM64，Docker Desktop 会自动处理架构兼容。无需额外配置。

如果遇到问题，可强制指定平台：
```yaml
# docker-compose.yml 中添加
services:
  redis:
    platform: linux/arm64
    ...
```

---

## 附录：API 速查表

### Agent API (端口 8000)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（Redis + Mock 连通性） |
| POST | `/api/v1/analyze` | 提交告警分析请求 |
| GET | `/api/v1/analyze/{trace_id}/status` | 查询分析状态 |
| GET | `/api/v1/analyze/{trace_id}/result` | 获取分析结果 |
| POST | `/api/v1/feedback` | 提交满意度反馈 |
| WS | `/api/v1/analyze/{trace_id}/stream` | WebSocket 实时进度流 |

### Mock API (端口 8001)

#### 通用

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/mock/health` | 模拟环境健康检查 |
| POST | `/api/v1/mock/reset` | 重置所有模拟器 |

#### 场景管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/mock/scenarios` | 列出 8 个预设场景 |
| GET | `/api/v1/mock/scenarios/{name}` | 获取场景详情 |
| POST | `/api/v1/mock/scenarios/{name}/apply` | 应用场景（故障注入） |
| POST | `/api/v1/mock/scenarios/{name}/run` | 端到端执行（注入+分析+验证） |

#### K8s

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/mock/k8s/deployments` | Deployment 列表 |
| GET | `/api/v1/mock/k8s/deployments/{name}` | Deployment 详情 |
| GET | `/api/v1/mock/k8s/deployments/{name}/pods` | Pod 列表 |
| GET | `/api/v1/mock/k8s/events` | K8s 事件列表 |
| POST | `/api/v1/mock/k8s/inject/pod-restart` | 注入 Pod 重启 |
| POST | `/api/v1/mock/k8s/inject/resource-pressure` | 注入资源压力 |
| POST | `/api/v1/mock/k8s/inject/pod-crash` | 注入 Pod 崩溃 |
| POST | `/api/v1/mock/k8s/scale` | 伸缩 Deployment |

#### DB (MySQL)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/mock/db/{instance}/metrics` | DB 指标 |
| GET | `/api/v1/mock/db/{instance}/slow-log` | DB 慢日志 |
| GET | `/api/v1/mock/db/{instance}/topology` | DB 拓扑 |
| POST | `/api/v1/mock/db/{instance}/inject/slave-delay` | 注入主从延迟 |
| POST | `/api/v1/mock/db/{instance}/inject/connection-pool` | 注入连接池耗尽 |
| POST | `/api/v1/mock/db/{instance}/inject/slow-query` | 注入慢查询 |
| POST | `/api/v1/mock/db/{instance}/inject/lock-wait` | 注入锁等待 |

#### Redis

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/mock/redis/{instance}/metrics` | Redis 指标 |
| GET | `/api/v1/mock/redis/{instance}/hotkeys` | Redis 热 Key |
| GET | `/api/v1/mock/redis/{instance}/topology` | Redis 拓扑 |
| POST | `/api/v1/mock/redis/{instance}/inject/memory-pressure` | 注入内存压力 |
| POST | `/api/v1/mock/redis/{instance}/inject/hit-rate-drop` | 注入命中率下降 |
| POST | `/api/v1/mock/redis/{instance}/inject/hotkey` | 注入热 Key |
| POST | `/api/v1/mock/redis/{instance}/inject/bigkey` | 注入大 Key |

#### Kafka

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/mock/kafka/{cluster}/topics/{topic}/lag` | 消费积压 |
| GET | `/api/v1/mock/kafka/{cluster}/metrics` | Kafka 指标 |
| POST | `/api/v1/mock/kafka/{cluster}/inject/consumer-offline` | 注入消费者离线 |
| POST | `/api/v1/mock/kafka/{cluster}/inject/rebalance` | 注入 Rebalance 风暴 |
| POST | `/api/v1/mock/kafka/{cluster}/inject/consume-rate-drop` | 注入消费速率下降 |

#### 微服务

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/mock/service/{name}/topology` | 服务拓扑 |
| GET | `/api/v1/mock/service/{name}/metrics/{metric}` | 服务指标 |
| GET | `/api/v1/mock/service/{name}/traces` | 调用链 |
| GET | `/api/v1/mock/service/{name}/logs` | 服务日志 |
| GET | `/api/v1/mock/service/{name}/changes` | 服务变更记录 |
| GET | `/api/v1/mock/service/{name}/alerts` | 服务关联告警 |
| POST | `/api/v1/mock/service/{name}/inject/timeout` | 注入超时 |
| POST | `/api/v1/mock/service/{name}/inject/traffic-spike` | 注入流量突增 |
| POST | `/api/v1/mock/service/{name}/inject/error-rate` | 注入错误率突增 |

### 分析请求体格式

```json
{
  "alert_id": "alt-001",
  "service_name": "order-service",
  "alert_type": "timeout",
  "severity": "P1",
  "timestamp": "2026-07-22T10:00:00Z",
  "description": "接口超时",
  "labels": {
    "cluster": "prod-cluster-01",
    "env": "production",
    "app": "order"
  }
}
```

### 环境变量速查

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `APP_ENV` | development | 运行环境 |
| `APP_HOST` | 0.0.0.0 | 监听地址（Docker 内无需修改） |
| `APP_PORT` | 8000 | Agent 服务端口 |
| `APP_EXTERNAL_HOST` | localhost | 外部访问地址（生成反馈/WebSocket URL） |
| `AGENT_URL` | http://localhost:8000 | Agent 服务 URL（Mock 容器内调用时使用） |
| `LOG_LEVEL` | INFO | 日志级别 |
| `LLM_API_BASE` | http://localhost:11434/v1 | LLM API 地址 |
| `LLM_API_KEY` | (空) | LLM API Key |
| `LLM_MODEL` | gpt-4o | LLM 模型名 |
| `LLM_MAX_TOKENS` | 4096 | LLM 最大 token 数 |
| `LLM_TIMEOUT` | 30 | LLM 调用超时（秒） |
| `REDIS_HOST` | localhost | Redis 地址 |
| `REDIS_PORT` | 6379 | Redis 端口 |
| `REDIS_DB` | 0 | Redis 数据库编号 |
| `REDIS_PASSWORD` | (空) | Redis 密码 |
| `KAFKA_BOOTSTRAP_SERVERS` | localhost:9092 | Kafka 地址（仅生产环境使用） |
| `KAFKA_FEEDBACK_TOPIC` | deeprca-feedback | 反馈推送 Kafka topic |
| `ANALYSIS_TIMEOUT` | 60 | 分析超时（秒） |
| `TOOL_CALL_TIMEOUT` | 10 | 工具调用超时（秒） |
| `MAX_CONCURRENT_TASKS` | 10 | 最大并发任务数 |
| `MOCK_ENV_ENABLED` | true | 是否启用 Mock 模式 |
| `MOCK_K8S_API` | http://localhost:8001 | Mock K8s API 地址 |
| `MOCK_MONITOR_API` | http://localhost:8001 | Mock 监控 API 地址（Mock 模式下被工具层和 L2 专家使用） |
| `MOCK_LOG_API` | http://localhost:8001 | Mock 日志 API 地址（Mock 模式下被日志工具使用） |
| `MOCK_CHANGE_API` | http://localhost:8001 | Mock 变更 API 地址（Mock 模式下被变更工具使用） |


