# DeepRCA-Agent 总体架构 PRD

| 字段 | 内容 |
|------|------|
| 文档版本 | v1.0 |
| 创建日期 | 2026-07-10 |
| 状态 | Draft |
| 负责人 | - |

## 1. 背景与目标

### 1.1 背景

AIOps 故障处理助手是面向分布式系统的智能化故障分析 Agent 系统，目标是将日常故障分析从人工处理向智能化升级。通过集成监控系统生态（告警平台、性能监控、通讯平台等），利用自动化任务拆解、多领域下钻分析、实时信息总结，缩短故障 MTTR（Mean Time To Recovery）。

### 1.2 核心目标

- 构建基于 LangChain + LangGraph 的多 Agent 协同推理架构
- 实现故障根因命中率 ≥ 50%，关键线索命中率 ≥ 75%
- 支持并发任务处理，端到端分析响应时间 ≤ 60s
- 提供验证接口，后续接入微服务系统（含 K8s 模拟与多中间件模拟）

### 1.3 技术选型

| 层面 | 选型 | 说明 |
|------|------|------|
| Agent 编排框架 | LangGraph | 状态机驱动的多 Agent 编排，支持子 Agent 派生、中间件钩子 |
| Agent 基座 | LangChain | 工具调用、记忆管理、Prompt 模板 |
| 开发语言 | Python 3.11+ | 异步生态成熟 |
| LLM 接口 | OpenAI 兼容 | 支持 GPT-4o / Claude / 本地模型 |
| 数据处理 | Pandas / NumPy | 时序指标分析 |
| 异常检测 | 四分位+波动算法 | 替代大模型做异常检测，降低幻觉 |
| 缓存 | Redis / Squirrel | 中间结果缓存、反馈数据存储 |
| 消息队列 | Kafka / Mafka | 延迟消息机制（满意度推送） |
| API 框架 | FastAPI | RESTful 接口、WebSocket 实时推送 |

## 2. 系统架构

### 2.1 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        触发层 (Invocation)                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │
│  │ 告警回调  │  │ API 接口  │  │ IM 消息  │  │ 定时巡检     │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘    │
│       └──────────────┴──────────────┴──────────────┘            │
│                          │                                       │
├──────────────────────────▼───────────────────────────────────────┤
│                    编排层 (Orchestration)                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              LangGraph State Machine                     │   │
│  │  ┌─────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐ │   │
│  │  │ 接收告警 │──▶│ 任务拆解  │──▶│ 并发分析  │──▶│ 根因定位│ │   │
│  │  └─────────┘   └──────────┘   └──────────┘   └────────┘ │   │
│  │                     │                      │             │   │
│  │              ┌──────▼──────┐        ┌──────▼──────┐      │   │
│  │              │ 通用分析Agent │        │ 领域专家Agent │      │   │
│  │              │ (6个维度)    │        │ (4个领域)    │      │   │
│  │              └─────────────┘        └─────────────┘      │   │
│  └──────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────┤
│                      工具层 (Tools)                               │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌─────┐│
│  │指标查询 │ │日志查询 │ │变更查询 │ │调用链  │ │告警查询 │ │拓扑 ││
│  └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └─────┘│
├──────────────────────────────────────────────────────────────────┤
│                   数据/集成层 (Data)                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │
│  │ 监控平台  │  │ 日志平台  │  │ 变更平台  │  │ 验证模拟环境  │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 多 Agent 协同架构

参考 Open-SWE 的 Subagent + Middleware 模式，DeepRCA-Agent 采用三层 Agent 架构：

**第一层：通用分析 Agent（Coordinator Agent）**

通用分析 Agent 作为编排主体，负责接收告警事件、拆解分析任务、调度领域专家子 Agent。采用 LangGraph 的 `StateGraph` 构建状态机，定义以下节点：

| 节点 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `intake` | 接收告警，提取关键字段（服务名、告警类型、时间窗口） | AlertEvent | ParsedAlert |
| `planner` | 任务拆解，生成分析维度列表（变更/上游/下游/集群/日志/问题） | ParsedAlert | TaskPlan[] |
| `dispatcher` | 并发派发分析任务到子 Agent | TaskPlan[] | SubAgentTasks[] |
| `collector` | 汇聚子 Agent 分析结果 | SubAgentResults[] | CollectedEvidence |
| `root_cause` | 根因定位，融合告警+专家经验 | CollectedEvidence | RootCauseResult |
| `reporter` | 生成分析报告，推送通知 | RootCauseResult | AnalysisReport |

**第二层：领域专家子 Agent（Domain Expert Agents）**

每个领域专家 Agent 是独立的 LangGraph 子图，可被通用分析 Agent 通过 `task` 工具派生：

| 子 Agent | 分析维度 | 核心工具 |
|----------|----------|----------|
| DB Expert | 数据库慢查询、连接池、锁等待、主从延迟 | `query_db_metrics`, `query_slow_log`, `query_db_topology` |
| Redis Expert | 内存使用、热点 Key、大 Key、命中率 | `query_redis_metrics`, `query_hotkey`, `query_redis_topology` |
| Mafka Expert | 消费延迟、积压、生产消费速率、Rebalance | `query_mafka_metrics`, `query_consumer_lag` |
| RPC Expert | 调用链路、失败率、RT 突变、依赖拓扑 | `query_rpc_metrics`, `query_trace`, `query_dependency_graph` |

**第三层：根因定位 Agent（Root Cause Agent）**

根因定位 Agent 接收所有子 Agent 的分析结果，结合雷达告警和专家经验规则库，执行最终根因推理：

- 多维指标筛选：高 QPS、高失败率、TP99 突变
- 多维度对比：周同比、日环比
- 过滤低影响抖动
- 融合调用链路 + 雷达告警 + 专家经验规则
- 输出根因结论 + 置信度 + 证据链

### 2.3 LangGraph 状态设计

```python
from typing import TypedDict, List, Optional, Annotated
from langgraph.graph import StateGraph, MessagesState
import operator

class AlertEvent(TypedDict):
    alert_id: str
    service_name: str
    alert_type: str          # timeout / error_rate / resource / custom
    severity: str            # P0 / P1 / P2 / P3
    timestamp: str
    description: str
    labels: dict             # cluster, env, app, etc.

class SubAgentResult(TypedDict):
    agent_name: str
    dimension: str           # change / upstream / downstream / cluster / errorlog / problem
    findings: List[dict]     # 发现的异常线索
    confidence: float        # 0.0 ~ 1.0
    evidence: List[str]      # 证据链
    timestamp: str

class DeepRCAState(TypedDict):
    # 输入
    alert: AlertEvent
    # 任务计划
    task_plan: List[dict]
    # 并发分析结果（使用 reducer 合并）
    sub_agent_results: Annotated[List[SubAgentResult], operator.add]
    # 根因结果
    root_cause: Optional[dict]
    # 分析报告
    report: Optional[str]
    # 消息历史
    messages: Annotated[list, operator.add]
    # 元数据
    trace_id: str
    start_time: str
    status: str              # running / completed / failed
```

### 2.4 中间件设计

参考 Open-SWE 的 Middleware 模式，在 Agent 循环中插入确定性钩子：

| 中间件 | 触发时机 | 职责 |
|--------|----------|------|
| `AlertQueueMiddleware` | before_model | 检查告警队列，注入新到达的关联告警 |
| `TimeoutGuardMiddleware` | before_model | 检查分析超时，超时则强制进入根因定位 |
| `EvidenceCollectorMiddleware` | after_tool | 收集每个工具调用的结果，写入证据池 |
| `ErrorRecoveryMiddleware` | after_tool | 工具调用失败时的降级处理 |
| `SatisfactionPushMiddleware` | after_agent | 分析完成后 30 分钟触发满意度推送 |

### 2.5 并发处理设计

采用 `asyncio` + `ThreadPoolExecutor` + `CompletableFuture` 模式进行任务的批次分析与并发处理：

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

class ParallelAnalyzer:
    """并发分析调度器"""

    def __init__(self, max_workers: int = 10):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    async def analyze_parallel(
        self,
        tasks: List[TaskPlan],
        state: DeepRCAState
    ) -> List[SubAgentResult]:
        """并发执行多个维度的分析任务"""
        loop = asyncio.get_event_loop()

        async def run_sub_agent(task: TaskPlan) -> SubAgentResult:
            # 通过 LangGraph 子图执行领域分析
            return await loop.run_in_executor(
                self.executor,
                self._execute_sub_agent,
                task,
                state
            )

        results = await asyncio.gather(
            *[run_sub_agent(task) for task in tasks],
            return_exceptions=True
        )
        return [r for r in results if not isinstance(r, Exception)]
```

## 3. 项目结构

```
DeepRCA-Agent/
├── prds/                           # 产品需求文档
│   ├── 01_overview_prd.md          # 总体架构 PRD
│   ├── 02_general_analyzer_prd.md  # 通用分析 Agent PRD
│   ├── 03_domain_expert_prd.md     # 领域专家子 Agent PRD
│   ├── 04_root_cause_prd.md        # 根因定位 Agent PRD
│   ├── 05_mock_env_prd.md          # 验证接口与模拟环境 PRD
│   └── 06_containerization_prd.md  # 容器化部署与冒烟测试 PRD
├── src/
│   └── deeprca/
│       ├── __init__.py
│       ├── graph/                  # LangGraph 图定义
│       │   ├── __init__.py
│       │   ├── state.py            # 状态定义
│       │   ├── main_graph.py       # 主编排图
│       │   └── subgraphs/          # 子 Agent 子图
│       │       ├── db_expert.py
│       │       ├── redis_expert.py
│       │       ├── mafka_expert.py
│       │       └── rpc_expert.py
│       ├── agents/                 # Agent 定义
│       │   ├── __init__.py
│       │   ├── coordinator.py      # 通用分析 Agent
│       │   ├── root_cause.py       # 根因定位 Agent
│       │   └── experts/            # 领域专家 Agent
│       ├── tools/                  # 工具集
│       │   ├── __init__.py
│       │   ├── metrics.py          # 指标查询工具
│       │   ├── logs.py             # 日志查询工具
│       │   ├── changes.py          # 变更查询工具
│       │   ├── traces.py           # 调用链工具
│       │   ├── alerts.py           # 告警查询工具
│       │   └── topology.py         # 拓扑查询工具
│       ├── middleware/             # 中间件
│       │   ├── __init__.py
│       │   ├── alert_queue.py
│       │   ├── timeout_guard.py
│       │   ├── evidence_collector.py
│       │   ├── error_recovery.py
│       │   └── satisfaction_push.py
│       ├── detection/              # 异常检测算法
│       │   ├── __init__.py
│       │   ├── quantile.py         # 四分位+波动算法
│       │   ├── anomaly_detector.py # 通用异常检测
│       │   └── filters.py          # 指标筛选过滤器
│       ├── models/                 # 数据模型
│       │   ├── __init__.py
│       │   ├── alert.py
│       │   ├── evidence.py
│       │   └── report.py
│       ├── config/                 # 配置
│       │   ├── __init__.py
│       │   └── settings.py
│       └── api/                    # API 接口
│           ├── __init__.py
│           ├── server.py           # FastAPI 服务
│           ├── routes.py           # 路由定义
│           └── websocket.py        # WebSocket 实时推送
├── tests/                          # 测试
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── mock_env/                       # 验证模拟环境
│   ├── k8s_simulator/              # K8s 模拟器
│   ├── middleware_simulator/       # 中间件模拟器
│   └── microservice_simulator/     # 微服务模拟器
├── docker-compose.yml              # 一键启动
├── requirements.txt
├── README.md
└── .gitignore
```

## 4. 数据流

```
告警事件
  │
  ▼
intake (解析告警)
  │
  ▼
planner (任务拆解: 变更/上游/下游/集群/日志/问题)
  │
  ├──▶ [并发] change_agent → 查变更平台 → 变更线索
  ├──▶ [并发] upstream_agent → 查上游流量指标 → 流量异常线索
  ├──▶ [并发] downstream_agent → 查下游依赖 → 依赖异常线索
  ├──▶ [并发] cluster_agent → 查集群状态 → 资源异常线索
  ├──▶ [并发] errorlog_agent → 查 ErrorLog → 错误线索
  ├──▶ [并发] problem_agent → 查已知问题 → 问题匹配线索
  │
  ▼
collector (汇聚所有线索)
  │
  ▼
root_cause (根因定位)
  │  ├── 多维指标筛选 (高QPS/高失败率/TP99突变)
  │  ├── 多维度对比 (周同比/日环比)
  │  ├── 过滤低影响抖动
  │  ├── 融合调用链路 + 雷达告警 + 专家经验
  │  └── 输出根因 + 置信度 + 证据链
  │
  ▼
reporter (生成报告 + 推送通知)
  │
  ▼
[30分钟后] 满意度推送 → 收集反馈 → 闭环
```

## 5. 关键设计决策

### 5.1 为什么用四分位+波动算法替代大模型做异常检测

大模型在数值推理和时间序列异常检测上存在幻觉问题，容易将正常波动误判为异常。四分位+波动算法是确定性算法，基于统计学原理，结果可解释、可复现，适用于：
- QPS 突变检测
- TP99 延迟突变检测
- 错误率突变检测

大模型仅用于自然语言理解任务（如告警描述解析、报告生成），不参与数值判断。

### 5.2 为什么用 LangGraph 而非纯 LangChain

LangGraph 提供了状态机驱动的编排能力，支持：
- 显式状态转移（而非隐式 Chain 链式调用）
- 子图嵌套（子 Agent 作为独立子图）
- 中间件钩子（before_model / after_tool）
- 人机交互节点（审批、确认）
- 持久化检查点（中断恢复）

这些特性对于故障分析这种多步骤、可中断、需并发编排的场景至关重要。

### 5.3 为什么采用三层 Agent 架构

单层 Agent 无法兼顾通用性和专业性。三层架构实现了关注点分离：
- 第一层负责编排和调度，不涉及具体领域知识
- 第二层封装领域专业知识，可独立扩展
- 第三层专注根因推理，融合多源信息

这与 Open-SWE 的 Subagent + Middleware 模式一致，也符合 AIOps 的「通用分析 Agent → 领域专家子 Agent → 根因定位 Agent」分层。

## 6. 性能指标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 端到端分析延迟 | ≤ 60s | 从告警接收到报告生成 |
| 根因命中率 | ≥ 50% | 根因与人工确认一致 |
| 关键线索命中率 | ≥ 75% | 关键线索被采纳 |
| 并发分析维度 | 6+ | 同时分析6个维度 |
| 工具调用超时 | 10s | 单次工具调用超时阈值 |
| 分析超时兜底 | 120s | 整体分析超时强制输出 |

## 7. 里程碑

| 里程碑 | 内容 | 预估周期 |
|--------|------|----------|
| M1 | 基础框架搭建：LangGraph 图定义、状态模型、API 骨架 | 1 周 |
| M2 | 通用分析 Agent + 6 维度分析工具 | 2 周 |
| M3 | 领域专家子 Agent（DB/Redis/Mafka/RPC） | 2 周 |
| M4 | 根因定位 Agent + 异常检测算法 | 1.5 周 |
| M5 | 验证模拟环境（K8s + 中间件模拟） | 1.5 周 |
| M6 | 容器化部署 + 冒烟测试框架（Docker Compose Profile + smoke-test 容器） | 0.5 周 |
| M7 | 满意度反馈闭环 | 1 周 |
| M8 | 集成测试 + 性能优化 | 1 周 |
