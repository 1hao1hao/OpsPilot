# DeepRCA-Agent 容器化部署与冒烟测试 PRD

| 字段 | 内容 |
|------|------|
| 文档版本 | v1.0 |
| 创建日期 | 2026-07-10 |
| 状态 | Draft |
| 负责人 | - |
| 关联文档 | 01_overview_prd.md, 02_general_analyzer_prd.md, 03_domain_expert_prd.md, 04_root_cause_prd.md, 05_mock_env_prd.md |

## 1. 概述

本文档定义 DeepRCA-Agent 系统的容器化部署方案与冒烟测试工作流。系统包含三个独立服务组件（Agent 服务、模拟环境、Redis），通过 Docker Compose 编排统一管理。针对逐功能开发中的冒烟测试需求，提供基于 Docker Compose Profile 的按需启动能力，使开发者可以快速拉起最小验证环境，对单个功能模块进行端到端验证。

### 1.1 设计目标

- **一键启停**：`docker compose up -d` 即可拉起完整环境，`docker compose down` 一键清理
- **按需启动**：通过 Profile 机制支持只启动冒烟测试所需的最小组件集
- **环境隔离**：每个容器独立运行，端口/网络/存储互不干扰
- **快速迭代**：代码变更后 `docker compose up -d --build` 重建镜像，秒级生效
- **冒烟测试友好**：内置健康检查、就绪探针、测试脚本入口

### 1.2 服务组件矩阵

| 服务 | 镜像 | 端口 | 依赖 | Profile | 说明 |
|------|------|------|------|---------|------|
| `deeprca-agent` | 自建 (Python 3.11-slim) | 8000 | redis | agent, full, smoke | 核心 Agent 服务 |
| `mock-env` | 自建 (Python 3.11-slim) | 8001 | - | mock, full, smoke | 模拟环境服务 |
| `redis` | redis:7-alpine | 6379 | - | agent, full, smoke, redis-only | 缓存与状态存储 |
| `smoke-test` | 自建 (Python 3.11-slim) | - | deeprca-agent, mock-env | smoke | 冒烟测试执行器（run-to-completion） |

## 2. 容器化架构

```
┌──────────────────────────────────────────────────────────┐
│                   Docker Network (deeprca-net)            │
│                                                          │
│  ┌─────────────────┐     ┌─────────────────┐            │
│  │  deeprca-agent   │     │    mock-env      │            │
│  │  :8000           │     │    :8001         │            │
│  │  FastAPI + WS    │     │    FastAPI       │            │
│  │                  │     │                  │            │
│  │  health: /health │     │  health: /health │            │
│  └───────┬──────────┘     └──────────────────┘            │
│          │                                                │
│          │ depends_on (healthy)                           │
│          ▼                                                │
│  ┌─────────────────┐     ┌─────────────────┐            │
│  │     redis        │     │  smoke-test      │            │
│  │  :6379           │     │  (一次性运行)     │            │
│  │  health: ping    │     │  pytest + curl   │            │
│  └─────────────────┘     └─────────────────┘            │
│                                                          │
├──────────────────────────────────────────────────────────┤
│                   Volumes                                 │
│  deeprca-redis-data  → /data (Redis 持久化)               │
│  deeprca-logs        → /app/logs (日志共享)               │
└──────────────────────────────────────────────────────────┘
```

### 2.1 网络设计

- 自定义桥接网络 `deeprca-net`，容器间通过服务名互访
- `deeprca-agent` 通过 `redis:6379` 访问 Redis（Docker DNS 解析）
- `deeprca-agent` 通过 `mock-env:8001` 访问模拟环境
- 外部访问通过端口映射：`localhost:8000` → Agent，`localhost:8001` → Mock

### 2.2 数据持久化

| Volume | 挂载点 | 用途 |
|--------|--------|------|
| `deeprca-redis-data` | redis:/data | Redis 数据持久化（分析状态、缓存） |
| `deeprca-logs` | deeprca-agent:/app/logs, mock-env:/app/logs | 日志文件共享卷 |

## 3. Dockerfile 规范

### 3.1 Agent 服务 Dockerfile

```dockerfile
# deeprca/Dockerfile
FROM python:3.11-slim AS base

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖层（利用 Docker 缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 代码层
COPY deeprca/ ./deeprca/
COPY mock_env/ ./mock_env/

# 非 root 用户
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=15s \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "deeprca.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 3.2 模拟环境 Dockerfile

```dockerfile
# mock_env/Dockerfile
FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-mock.txt .
RUN pip install --no-cache-dir -r requirements-mock.txt

COPY mock_env/ ./mock_env/

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8001/api/v1/mock/health || exit 1

CMD ["uvicorn", "mock_env.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

### 3.3 冒烟测试 Dockerfile

```dockerfile
# tests/Dockerfile
FROM python:3.11-slim AS base

WORKDIR /app

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY tests/ ./tests/
COPY tests/smoke/ ./tests/smoke/

# 入口脚本：等待服务就绪后执行测试
COPY tests/smoke/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["pytest", "tests/smoke/", "-v", "--tb=short"]
```

### 3.4 镜像构建策略

采用分层缓存优化构建速度：

```
Base Image (python:3.11-slim)
  └── System Deps (curl)
       └── Python Deps (requirements.txt)  ← 很少变动，缓存命中率高
            └── Application Code            ← 每次代码变更只重建此层
```

## 4. Docker Compose 编排

### 4.1 完整 docker-compose.yml

```yaml
# docker-compose.yml
version: "3.8"

networks:
  deeprca-net:
    driver: bridge

volumes:
  deeprca-redis-data:
  deeprca-logs:

services:
  # ─── Redis ───
  redis:
    image: redis:7-alpine
    container_name: deeprca-redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    volumes:
      - deeprca-redis-data:/data
    networks:
      - deeprca-net
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
      start_period: 5s
    profiles:
      - redis-only
      - agent
      - full
      - smoke

  # ─── Agent 服务 ───
  deeprca-agent:
    build:
      context: .
      dockerfile: deeprca/Dockerfile
    container_name: deeprca-agent
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - LLM_API_KEY=${LLM_API_KEY:-}
      - LLM_MODEL=${LLM_MODEL:-gpt-4o}
      - LLM_BASE_URL=${LLM_BASE_URL:-https://api.openai.com/v1}
      - REDIS_URL=redis://redis:6379/0
      - MOCK_ENV_URL=http://mock-env:8001
      - MOCK_ENV_ENABLED=${MOCK_ENV_ENABLED:-true}
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
      - ANALYSIS_TIMEOUT=${ANALYSIS_TIMEOUT:-120}
    volumes:
      - deeprca-logs:/app/logs
      - ./.env:/app/.env:ro
    networks:
      - deeprca-net
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 15s
    profiles:
      - agent
      - full
      - smoke

  # ─── 模拟环境 ───
  mock-env:
    build:
      context: .
      dockerfile: mock_env/Dockerfile
    container_name: deeprca-mock-env
    restart: unless-stopped
    ports:
      - "8001:8001"
    environment:
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
    volumes:
      - deeprca-logs:/app/logs
    networks:
      - deeprca-net
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8001/api/v1/mock/health"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 10s
    profiles:
      - mock
      - full
      - smoke

  # ─── 冒烟测试执行器 ───
  smoke-test:
    build:
      context: .
      dockerfile: tests/Dockerfile
    container_name: deeprca-smoke-test
    environment:
      - AGENT_URL=http://deeprca-agent:8000
      - MOCK_URL=http://mock-env:8001
    networks:
      - deeprca-net
    depends_on:
      deeprca-agent:
        condition: service_healthy
      mock-env:
        condition: service_healthy
    profiles:
      - smoke
```

### 4.2 Profile 使用说明

| Profile | 启动服务 | 命令 | 适用场景 |
|---------|----------|------|----------|
| `redis-only` | redis | `docker compose --profile redis-only up -d` | 仅需 Redis 缓存调试 |
| `agent` | redis + agent | `docker compose --profile agent up -d` | Agent 功能开发（对接外部 Mock） |
| `mock` | mock-env | `docker compose --profile mock up -d` | 模拟环境独立开发调试 |
| `full` | redis + agent + mock-env | `docker compose --profile full up -d` | 完整环境联调 |
| `smoke` | redis + agent + mock-env + smoke-test | `docker compose --profile smoke up --abort-on-container-exit` | 冒烟测试自动执行 |

> **说明**：`smoke-test` 容器是 run-to-completion 模式，测试执行完毕后自动退出。使用 `--abort-on-container-exit` 确保测试容器退出后其他容器也随之停止。

## 5. 冒烟测试工作流

### 5.1 逐功能冒烟测试流程

```
开发完成一个功能模块
  │
  ▼
编写该模块的冒烟测试用例 (tests/smoke/test_{module}.py)
  │
  ▼
docker compose --profile smoke up --build --abort-on-container-exit
  │
  ├── Redis 启动 → 等待健康
  ├── Mock Env 启动 → 等待健康
  ├── Agent 启动 → 等待健康
  ├── Smoke Test 启动 → 执行 pytest
  │     ├── test_health.py        → 验证服务存活
  │     ├── test_{module}.py      → 验证新功能
  │     └── test_scenarios.py     → 验证端到端场景
  │
  ▼
查看测试报告 (stdout + logs/deeprca-logs/)
  │
  ├── PASS → 继续下一个功能
  └── FAIL → 修复后重新构建测试
```

### 5.2 冒烟测试目录结构

```
tests/
├── smoke/
│   ├── __init__.py
│   ├── conftest.py                 # 公共 fixture（服务地址、HTTP 客户端）
│   ├── entrypoint.sh               # 容器入口脚本（等待服务就绪）
│   ├── test_health.py              # 服务健康检查
│   ├── test_intake.py              # L1 Intake 节点冒烟
│   ├── test_planner.py             # L1 Planner 节点冒烟
│   ├── test_dispatcher.py          # L1 Dispatcher 并发调度冒烟
│   ├── test_db_expert.py           # L2 DB 专家 Agent 冒烟
│   ├── test_redis_expert.py        # L2 Redis 专家 Agent 冒烟
│   ├── test_root_cause.py          # L3 根因定位冒烟
│   ├── test_mock_k8s.py            # K8s 模拟器冒烟
│   ├── test_mock_db.py             # DB 模拟器冒烟
│   ├── test_mock_redis.py          # Redis 模拟器冒烟
│   ├── test_mock_kafka.py          # Kafka 模拟器冒烟
│   └── test_e2e_scenarios.py       # 端到端场景冒烟（5 个预设场景）
├── unit/                           # 单元测试
└── integration/                    # 集成测试
```

### 5.3 冒烟测试入口脚本

```bash
#!/bin/bash
# tests/smoke/entrypoint.sh
# 等待 Agent 和 Mock 服务就绪后执行测试

set -e

AGENT_URL="${AGENT_URL:-http://deeprca-agent:8000}"
MOCK_URL="${MOCK_URL:-http://mock-env:8001}"
MAX_RETRIES=30
RETRY_INTERVAL=2

echo "[smoke-test] Waiting for services to be ready..."

wait_for_service() {
    local url=$1
    local name=$2
    local retries=0
    while [ $retries -lt $MAX_RETRIES ]; do
        if curl -sf "$url" > /dev/null 2>&1; then
            echo "[smoke-test] $name is ready ($url)"
            return 0
        fi
        retries=$((retries + 1))
        sleep $RETRY_INTERVAL
    done
    echo "[smoke-test] ERROR: $name not ready after $MAX_RETRIES retries"
    return 1
}

wait_for_service "$AGENT_URL/health" "Agent"
wait_for_service "$MOCK_URL/api/v1/mock/health" "Mock Env"

echo "[smoke-test] All services ready, running tests..."
exec "$@"
```

### 5.4 冒烟测试公共 Fixture

```python
# tests/smoke/conftest.py
import pytest
import httpx
import asyncio

AGENT_URL = "http://deeprca-agent:8000"
MOCK_URL = "http://mock-env:8001"


@pytest.fixture
def agent_client():
    """Agent 服务 HTTP 客户端"""
    with httpx.Client(base_url=AGENT_URL, timeout=30) as client:
        yield client


@pytest.fixture
def mock_client():
    """Mock 环境 HTTP 客户端"""
    with httpx.Client(base_url=MOCK_URL, timeout=10) as client:
        yield client


@pytest.fixture
def reset_mock(mock_client):
    """每个测试前重置模拟环境"""
    mock_client.post("/api/v1/mock/reset")
    yield


@pytest.fixture
def run_scenario(mock_client):
    """执行预设场景并返回结果"""
    def _run(scenario_name: str):
        resp = mock_client.post(f"/api/v1/mock/scenarios/{scenario_name}/run", timeout=120)
        return resp.json()
    return _run
```

### 5.5 冒烟测试示例

```python
# tests/smoke/test_health.py
def test_agent_health(agent_client):
    """验证 Agent 服务健康"""
    resp = agent_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"


def test_mock_health(mock_client):
    """验证 Mock 环境健康"""
    resp = mock_client.get("/api/v1/mock/health")
    assert resp.status_code == 200


# tests/smoke/test_e2e_scenarios.py
import pytest

SCENARIOS = [
    "db_slave_delay_timeout",
    "oom_restart",
    "kafka_consumer_lag",
    "change_induced_failure",
    "redis_memory_pressure",
]

@pytest.mark.parametrize("scenario", SCENARIOS)
def test_e2e_scenario(reset_mock, run_scenario, scenario):
    """端到端场景冒烟测试"""
    result = run_scenario(scenario)
    assert result["status"] == "passed", f"Scenario {scenario} failed"
    assert result["root_cause_matched"] is True
    assert result["actual_confidence"] >= result["expected_confidence_min"]


# tests/smoke/test_db_expert.py
def test_db_expert_slave_delay(reset_mock, mock_client, agent_client):
    """DB 专家 Agent — 主从延迟场景冒烟"""
    # 1. 注入故障
    mock_client.post("/api/v1/mock/db/mysql-prod-01/inject/slave-delay",
                     json={"delay_seconds": 15.0})
    # 2. 提交分析
    resp = agent_client.post("/api/v1/analyze", json={
        "alert_id": "smoke-db-001",
        "service_name": "order-service",
        "alert_type": "timeout",
        "severity": "P1",
        "description": "order-service TP99 延迟突增至 800ms",
        "labels": {"cluster": "prod-cluster-01", "env": "production"},
    })
    trace_id = resp.json()["trace_id"]

    # 3. 等待分析完成
    import time
    for _ in range(30):
        status = agent_client.get(f"/api/v1/analyze/status/{trace_id}").json()
        if status["status"] in ("completed", "failed"):
            break
        time.sleep(2)

    # 4. 验证结果
    assert status["status"] == "completed"
    result = agent_client.get(f"/api/v1/analyze/result/{trace_id}").json()
    assert "root_cause" in result
    assert result["root_cause"]["confidence"] >= 0.5
```

## 6. 环境配置

### 6.1 .env 文件模板

```bash
# .env.example

# ─── LLM 配置 ───
LLM_API_KEY=your-api-key-here
LLM_MODEL=gpt-4o
LLM_BASE_URL=https://api.openai.com/v1

# ─── 服务配置 ───
LOG_LEVEL=INFO
ANALYSIS_TIMEOUT=120
MOCK_ENV_ENABLED=true

# ─── Redis（容器内通过服务名访问，本地开发用 localhost）───
# 容器内: redis://redis:6379/0
# 本地:   redis://localhost:6379/0
REDIS_URL=redis://localhost:6379/0

# ─── Mock 环境 ───
# 容器内: http://mock-env:8001
# 本地:   http://localhost:8001
MOCK_ENV_URL=http://localhost:8001
```

### 6.2 依赖文件

```
# requirements.txt — Agent 服务依赖
langchain>=0.2.0
langgraph>=0.1.0
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
redis>=5.0.0
httpx>=0.27.0
pydantic>=2.0.0
pandas>=2.2.0
numpy>=1.26.0
python-dotenv>=1.0.0

# requirements-mock.txt — 模拟环境依赖
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
pydantic>=2.0.0

# requirements-dev.txt — 开发/测试依赖
-r requirements.txt
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0
ruff>=0.4.0
mypy>=1.10.0
```

## 7. 开发工作流

### 7.1 日常开发循环

```bash
# 1. 启动依赖服务（Redis + Mock Env）
docker compose --profile mock up -d
docker compose --profile redis-only up -d

# 2. 本地开发运行 Agent（热重载）
uvicorn deeprca.main:app --reload --port 8000

# 3. 开发完成后执行冒烟测试
docker compose --profile smoke up --build --abort-on-container-exit

# 4. 查看测试输出
docker compose logs smoke-test

# 5. 清理
docker compose down -v
```

### 7.2 按功能模块冒烟测试

```bash
# 仅测试特定模块（通过 pytest -k 过滤）
docker compose run --rm \
    -e PYTEST_FILTER="test_db_expert" \
    smoke-test \
    pytest tests/smoke/ -v -k "test_db_expert"

# 仅测试健康检查
docker compose run --rm \
    smoke-test \
    pytest tests/smoke/test_health.py -v

# 仅测试端到端场景
docker compose run --rm \
    smoke-test \
    pytest tests/smoke/test_e2e_scenarios.py -v
```

### 7.3 完整环境联调

```bash
# 拉起全部服务
docker compose --profile full up -d --build

# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f deeprca-agent
docker compose logs -f mock-env

# 手动测试 API
curl http://localhost:8000/health
curl http://localhost:8001/api/v1/mock/health

# 运行端到端场景
curl -X POST http://localhost:8001/api/v1/mock/scenarios/db_slave_delay_timeout/run

# 清理
docker compose down -v
```

## 8. CI/CD 集成

### 8.1 GitHub Actions 冒烟测试

```yaml
# .github/workflows/smoke-test.yml
name: Smoke Test

on:
  push:
    branches: [main, dev]
  pull_request:
    branches: [main]

jobs:
  smoke-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Run smoke tests
        run: |
          docker compose --profile smoke up --build --abort-on-container-exit

      - name: Collect logs
        if: always()
        run: |
          docker compose logs > smoke-test-logs.txt

      - name: Upload logs
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: smoke-test-logs
          path: smoke-test-logs.txt
```

### 8.2 镜像构建与推送

```yaml
# .github/workflows/build-images.yml
name: Build Images

on:
  push:
    branches: [main]
    tags: ['v*']

jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        target: [deeprca-agent, mock-env]
    steps:
      - uses: actions/checkout@v4

      - name: Build image
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ${{ matrix.target == 'deeprca-agent' && 'deeprca/Dockerfile' || 'mock_env/Dockerfile' }}
          tags: ghcr.io/${{ github.repository }}/${{ matrix.target }}:${{ github.ref_name }}
          push: ${{ github.event_name != 'pull_request' }}
```

## 9. 健康检查设计

### 9.1 Agent 服务健康端点

```python
# deeprca/api/health.py
from fastapi import APIRouter, Depends
import redis
import httpx

router = APIRouter()

@router.get("/health")
async def health_check():
    """Agent 服务健康检查"""
    checks = {}

    # Redis 连通性
    try:
        r = redis.Redis.from_url("redis://redis:6379/0")
        r.ping()
        checks["redis"] = "healthy"
    except Exception:
        checks["redis"] = "unhealthy"

    # Mock 环境连通性
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://mock-env:8001/api/v1/mock/health", timeout=3)
            checks["mock_env"] = "healthy" if resp.status_code == 200 else "unhealthy"
    except Exception:
        checks["mock_env"] = "unhealthy"

    all_healthy = all(v == "healthy" for v in checks.values())
    return {
        "status": "healthy" if all_healthy else "degraded",
        "checks": checks,
    }
```

### 9.2 健康检查矩阵

| 服务 | 端点 | 检查内容 | 间隔 | 超时 | 重试 |
|------|------|----------|------|------|------|
| deeprca-agent | `GET /health` | Redis + Mock 连通性 | 10s | 5s | 3 |
| mock-env | `GET /api/v1/mock/health` | 模拟器状态 | 10s | 5s | 3 |
| redis | `redis-cli ping` | Redis PONG | 5s | 3s | 5 |

## 10. 冒烟测试与功能模块映射

| 功能模块 | 冒烟测试文件 | 验证内容 | 依赖 Profile |
|----------|-------------|----------|-------------|
| Intake 节点 | `test_intake.py` | 告警解析、字段提取、时间窗口推导 | smoke |
| Planner 节点 | `test_planner.py` | 六维度拆解、告警类型映射 | smoke |
| Dispatcher 节点 | `test_dispatcher.py` | 并发调度、超时降级、结果汇聚 | smoke |
| DB Expert Agent | `test_db_expert.py` | 慢查询/连接池/主从延迟检测 | smoke |
| Redis Expert Agent | `test_redis_expert.py` | 内存/命中率/热点Key检测 | smoke |
| Mafka Expert Agent | `test_mafka_expert.py` | 消费延迟/积压/Rebalance检测 | smoke |
| Root Cause Agent | `test_root_cause.py` | 异常检测+规则匹配+LLM推理 | smoke |
| K8s 模拟器 | `test_mock_k8s.py` | Pod/Deployment/故障注入 | smoke |
| DB 模拟器 | `test_mock_db.py` | 指标生成+故障注入 | smoke |
| Redis 模拟器 | `test_mock_redis.py` | 指标生成+故障注入 | smoke |
| Kafka 模拟器 | `test_mock_kafka.py` | 积压+消费者状态 | smoke |
| 端到端场景 | `test_e2e_scenarios.py` | 5 个预设场景全链路验证 | smoke |

## 11. 常用命令速查

```bash
# ─── 启动 ───
docker compose --profile full up -d              # 完整环境
docker compose --profile agent up -d              # Agent + Redis
docker compose --profile mock up -d               # 仅 Mock
docker compose --profile smoke up --abort-on-container-exit  # 冒烟测试

# ─── 构建 ───
docker compose --profile full up -d --build       # 重新构建并启动
docker compose build deeprca-agent                 # 仅构建 Agent 镜像

# ─── 日志 ───
docker compose logs -f deeprca-agent              # 跟踪 Agent 日志
docker compose logs --tail=100 mock-env           # 最近 100 行 Mock 日志

# ─── 状态 ───
docker compose ps                                 # 服务状态
docker compose ps --format json                   # JSON 格式

# ─── 清理 ───
docker compose down                               # 停止并删除容器
docker compose down -v                            # 同时删除 Volume
docker system prune -f                            # 清理悬空镜像

# ─── 调试 ───
docker compose exec deeprca-agent bash            # 进入 Agent 容器
docker compose exec mock-env python -c "..."      # 在 Mock 容器执行 Python
```

## 12. 注意事项

- **.env 文件**：容器内通过 `env_file` 或 volume 挂载加载，本地开发直接 `source .env`
- **端口冲突**：如本机已有 Redis/服务占用 6379/8000/8001，需在 `.env` 中修改映射端口
- **镜像大小**：基于 `python:3.11-slim`，Agent 镜像约 400MB，Mock 镜像约 200MB
- **构建缓存**：首次构建约 2-3 分钟，后续增量构建约 10-30 秒（仅代码层变更）
- **Windows 开发**：确保 Docker Desktop 启用了 WSL2 后端，Volume 挂载使用绝对路径
- **LLM API Key**：冒烟测试中如不需要真实 LLM 调用，可设置 `LLM_API_KEY=mock` 启用 mock 模式
