# DeepRCA-Agent 通用分析 Agent PRD

| 字段 | 内容 |
|------|------|
| 文档版本 | v1.0 |
| 创建日期 | 2026-07-10 |
| 状态 | Draft |
| 负责人 | - |
| 关联文档 | 01_overview_prd.md |

## 1. 概述

通用分析 Agent（Coordinator Agent）是 DeepRCA-Agent 系统的第一层 Agent，作为整个编排的主体。它负责接收告警事件、拆解分析任务、并发调度领域专家子 Agent、汇聚分析结果并生成最终报告。

本 PRD 详细定义通用分析 Agent 的六个核心节点（intake / planner / dispatcher / collector / root_cause / reporter）的输入输出、处理逻辑、工具接口和验证 API。

## 2. 节点详细设计

### 2.1 Intake 节点

**职责**：接收原始告警事件，解析并提取关键字段，标准化为内部数据结构。

**输入格式**：

```json
{
  "alert_id": "alert-20260710-001",
  "service_name": "order-service",
  "alert_type": "timeout",
  "severity": "P1",
  "timestamp": "2026-07-10T14:30:00+08:00",
  "description": "order-service TP99 延迟突增至 800ms",
  "labels": {
    "cluster": "prod-cluster-01",
    "env": "production",
    "app": "order-service",
    "namespace": "default",
    "pod": "order-service-7d4f6b-x2k9l"
  }
}
```

**处理逻辑**：

```python
from langgraph.graph import StateGraph
from deeprca.models.alert import AlertEvent, ParsedAlert

def intake_node(state: DeepRCAState) -> dict:
    """接收告警，提取关键字段并标准化"""
    alert = state["alert"]

    parsed = ParsedAlert(
        alert_id=alert["alert_id"],
        service_name=alert["service_name"],
        alert_type=alert["alert_type"],
        severity=alert["severity"],
        timestamp=alert["timestamp"],
        description=alert["description"],
        labels=alert.get("labels", {}),
        # 推导分析时间窗口
        time_window=_derive_time_window(alert),
        # 推导关联服务列表
        related_services=_extract_related_services(alert),
    )

    return {
        "alert": parsed,
        "status": "intake_completed",
        "messages": [HumanMessage(content=f"告警已接收: {parsed.service_name} - {parsed.alert_type}")],
    }

def _derive_time_window(alert: AlertEvent) -> dict:
    """根据告警类型推导分析时间窗口"""
    windows = {
        "timeout": {"before": "30m", "after": "5m"},
        "error_rate": {"before": "15m", "after": "5m"},
        "resource": {"before": "60m", "after": "5m"},
        "custom": {"before": "30m", "after": "5m"},
    }
    return windows.get(alert["alert_type"], windows["custom"])
```

**输出**：`ParsedAlert`，包含标准化告警字段 + 分析时间窗口 + 关联服务列表。

### 2.2 Planner 节点

**职责**：根据告警类型和严重程度，拆解分析任务，生成多维度分析计划。

**分析维度映射表**：

| 告警类型 | 触发维度 | 优先级排序 |
|----------|----------|------------|
| timeout | 变更 / 下游依赖 / 集群状态 / ErrorLog / 上游流量 / 已知问题 | 变更优先 |
| error_rate | 变更 / 下游依赖 / ErrorLog / 集群状态 / 上游流量 / 已知问题 | 变更+下游优先 |
| resource | 集群状态 / 变更 / ErrorLog / 已知问题 | 集群优先 |
| custom | 全部六维度 | 按默认顺序 |

**维度定义**：

```python
ANALYSIS_DIMENSIONS = {
    "change": {
        "name": "变更分析",
        "description": "检查最近变更（部署、配置、扩缩容）是否与告警时间吻合",
        "tools": ["query_recent_changes"],
        "priority": 1,
        "time_window": "24h",
    },
    "upstream": {
        "name": "上游流量分析",
        "description": "分析上游调用方 QPS、流量分布是否异常",
        "tools": ["query_metrics", "query_topology"],
        "priority": 2,
        "time_window": "1h",
    },
    "downstream": {
        "name": "下游依赖分析",
        "description": "分析下游依赖服务（DB/Redis/Mafka/RPC）健康状态",
        "tools": ["query_metrics", "query_trace", "query_topology"],
        "priority": 3,
        "time_window": "1h",
    },
    "cluster": {
        "name": "集群状态分析",
        "description": "检查集群资源（CPU/Memory/Network/Pod）是否异常",
        "tools": ["query_metrics", "query_topology"],
        "priority": 4,
        "time_window": "1h",
    },
    "errorlog": {
        "name": "错误日志分析",
        "description": "扫描 ErrorLog，提取异常堆栈和错误模式",
        "tools": ["query_error_logs"],
        "priority": 5,
        "time_window": "30m",
    },
    "problem": {
        "name": "已知问题匹配",
        "description": "匹配已知问题库（历史故障、FAQ、SOP）",
        "tools": ["query_related_alerts"],
        "priority": 6,
        "time_window": "7d",
    },
}
```

**处理逻辑**：

```python
def planner_node(state: DeepRCAState) -> dict:
    """任务拆解，生成分析维度列表"""
    alert = state["alert"]
    alert_type = alert["alert_type"]

    # 根据告警类型选择分析维度
    dimension_map = {
        "timeout": ["change", "downstream", "cluster", "errorlog", "upstream", "problem"],
        "error_rate": ["change", "downstream", "errorlog", "cluster", "upstream", "problem"],
        "resource": ["cluster", "change", "errorlog", "problem"],
        "custom": list(ANALYSIS_DIMENSIONS.keys()),
    }

    dimensions = dimension_map.get(alert_type, dimension_map["custom"])

    # 生成任务计划
    task_plan = []
    for dim in dimensions:
        config = ANALYSIS_DIMENSIONS[dim]
        task_plan.append({
            "task_id": f"task-{alert['alert_id']}-{dim}",
            "dimension": dim,
            "name": config["name"],
            "description": config["description"],
            "tools": config["tools"],
            "time_window": config["time_window"],
            "priority": config["priority"],
            "status": "pending",
        })

    return {
        "task_plan": task_plan,
        "status": "planning_completed",
    }
```

### 2.3 Dispatcher 节点

**职责**：并发派发分析任务到领域专家子 Agent，支持容错和超时控制。

**并发策略**：

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

class Dispatcher:
    """并发任务派发器"""

    def __init__(self, max_workers: int = 6, timeout: int = 30):
        self.max_workers = max_workers
        self.timeout = timeout
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    async def dispatch(
        self,
        tasks: List[dict],
        state: DeepRCAState
    ) -> List[SubAgentResult]:
        """并发执行多个维度的分析任务"""
        loop = asyncio.get_event_loop()

        async def run_task(task: dict) -> SubAgentResult:
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        self.executor,
                        self._execute_sub_agent,
                        task,
                        state
                    ),
                    timeout=self.timeout
                )
                task["status"] = "completed"
                return result
            except asyncio.TimeoutError:
                task["status"] = "timeout"
                return SubAgentResult(
                    agent_name=task["dimension"],
                    dimension=task["dimension"],
                    findings=[],
                    confidence=0.0,
                    evidence=[f"任务超时({self.timeout}s)，未获取到分析结果"],
                    timestamp=datetime.now().isoformat(),
                )
            except Exception as e:
                task["status"] = "failed"
                return SubAgentResult(
                    agent_name=task["dimension"],
                    dimension=task["dimension"],
                    findings=[],
                    confidence=0.0,
                    evidence=[f"任务执行失败: {str(e)}"],
                    timestamp=datetime.now().isoformat(),
                )

        results = await asyncio.gather(
            *[run_task(task) for task in tasks],
            return_exceptions=False
        )
        return results

    def _execute_sub_agent(self, task: dict, state: DeepRCAState) -> SubAgentResult:
        """执行单个领域专家子 Agent"""
        dimension = task["dimension"]
        # 根据维度选择子 Agent
        sub_agent_map = {
            "change": self._run_change_agent,
            "upstream": self._run_upstream_agent,
            "downstream": self._run_downstream_agent,
            "cluster": self._run_cluster_agent,
            "errorlog": self._run_errorlog_agent,
            "problem": self._run_problem_agent,
        }
        executor = sub_agent_map.get(dimension)
        if executor:
            return executor(task, state)
        raise ValueError(f"未知分析维度: {dimension}")
```

**容错策略**：

| 异常类型 | 处理方式 | 说明 |
|----------|----------|------|
| 工具调用超时 | 降级返回空结果 | 记录超时日志，不影响其他维度 |
| 子 Agent 异常 | 返回错误证据 | 错误信息写入 evidence |
| 部分维度失败 | 继续执行 | 已成功的维度结果正常汇聚 |
| 全部维度失败 | 触发降级模式 | 直接进入 root_cause 节点，基于告警描述生成初步报告 |

### 2.4 Collector 节点

**职责**：汇聚所有子 Agent 的分析结果，构建证据池，按置信度和优先级排序。

**证据池结构**：

```python
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum

class EvidenceLevel(Enum):
    HIGH = "high"        # 直接证据（如变更记录匹配）
    MEDIUM = "medium"    # 间接证据（如指标异常波动）
    LOW = "low"          # 辅助证据（如日志中的疑似错误）

@dataclass
class Evidence:
    dimension: str           # 来源维度
    level: EvidenceLevel     # 证据等级
    content: str             # 证据内容
    confidence: float        # 置信度 0.0~1.0
    source: str              # 来源工具
    timestamp: str           # 采集时间
    metadata: dict = field(default_factory=dict)  # 额外元数据

class EvidencePool:
    """证据池 - 汇聚和管理所有分析证据"""

    def __init__(self):
        self._evidences: List[Evidence] = []
        self._by_dimension: Dict[str, List[Evidence]] = {}

    def add(self, evidence: Evidence):
        """添加证据"""
        self._evidences.append(evidence)
        dim = evidence.dimension
        if dim not in self._by_dimension:
            self._by_dimension[dim] = []
        self._by_dimension[dim].append(evidence)

    def add_from_sub_agent_result(self, result: SubAgentResult):
        """从子 Agent 结果中提取证据"""
        for finding in result.get("findings", []):
            level = EvidenceLevel.HIGH if finding.get("confidence", 0) > 0.7 else \
                    EvidenceLevel.MEDIUM if finding.get("confidence", 0) > 0.4 else \
                    EvidenceLevel.LOW
            self.add(Evidence(
                dimension=result["dimension"],
                level=level,
                content=finding.get("description", ""),
                confidence=finding.get("confidence", 0.0),
                source=result["agent_name"],
                timestamp=result.get("timestamp", ""),
                metadata=finding.get("metadata", {}),
            ))
        # 子 Agent 直接提供的证据链
        for ev_text in result.get("evidence", []):
            self.add(Evidence(
                dimension=result["dimension"],
                level=EvidenceLevel.MEDIUM,
                content=ev_text,
                confidence=result.get("confidence", 0.0),
                source=result["agent_name"],
                timestamp=result.get("timestamp", ""),
            ))

    def get_sorted(self) -> List[Evidence]:
        """按置信度和证据等级排序"""
        level_order = {EvidenceLevel.HIGH: 3, EvidenceLevel.MEDIUM: 2, EvidenceLevel.LOW: 1}
        return sorted(
            self._evidences,
            key=lambda e: (level_order[e.level], e.confidence),
            reverse=True
        )

    def get_by_dimension(self, dimension: str) -> List[Evidence]:
        """按维度获取证据"""
        return self._by_dimension.get(dimension, [])

    def summary(self) -> dict:
        """生成证据池摘要"""
        return {
            "total": len(self._evidences),
            "by_dimension": {dim: len(evs) for dim, evs in self._by_dimension.items()},
            "by_level": {
                level.value: len([e for e in self._evidences if e.level == level])
                for level in EvidenceLevel
            },
            "top_evidences": [
                {"content": e.content, "dimension": e.dimension, "confidence": e.confidence}
                for e in self.get_sorted()[:5]
            ],
        }
```

**处理逻辑**：

```python
def collector_node(state: DeepRCAState) -> dict:
    """汇聚子 Agent 分析结果"""
    pool = EvidencePool()

    for result in state.get("sub_agent_results", []):
        pool.add_from_sub_agent_result(result)

    collected_evidence = {
        "pool": pool,
        "summary": pool.summary(),
        "sorted_evidences": [
            {
                "dimension": e.dimension,
                "level": e.level.value,
                "content": e.content,
                "confidence": e.confidence,
                "source": e.source,
            }
            for e in pool.get_sorted()
        ],
    }

    return {
        "collected_evidence": collected_evidence,
        "status": "collection_completed",
    }
```

### 2.5 Root Cause 节点

**职责**：接收汇聚的证据，融合多源信息进行根因推理。（详细设计见 `04_root_cause_prd.md`）

**接口定义**：

```python
def root_cause_node(state: DeepRCAState) -> dict:
    """根因定位节点 - 调用根因定位 Agent"""
    from deeprca.agents.root_cause import RootCauseAgent

    agent = RootCauseAgent()
    result = agent.analyze(
        alert=state["alert"],
        evidence=state.get("collected_evidence", {}),
        sub_agent_results=state.get("sub_agent_results", []),
    )

    return {
        "root_cause": result,
        "status": "root_cause_completed",
    }
```

### 2.6 Reporter 节点

**职责**：生成结构化分析报告，推送通知，触发满意度反馈闭环。

**报告 JSON 结构**：

```json
{
  "trace_id": "trace-20260710-001",
  "alert_id": "alert-20260710-001",
  "service_name": "order-service",
  "alert_type": "timeout",
  "severity": "P1",
  "analysis_start_time": "2026-07-10T14:30:05+08:00",
  "analysis_end_time": "2026-07-10T14:30:45+08:00",
  "duration_seconds": 40,
  "root_cause": {
    "conclusion": "下游 DB 主从延迟导致 order-service 查询超时",
    "confidence": 0.85,
    "category": "downstream_dependency",
    "evidence_chain": [
      {
        "dimension": "downstream",
        "evidence": "DB slave delay 突增至 15s（正常 <1s）",
        "confidence": 0.92
      },
      {
        "dimension": "change",
        "evidence": "14:25 有 DB 配置变更，调整了 innodb_buffer_pool_size",
        "confidence": 0.75
      },
      {
        "dimension": "errorlog",
        "evidence": "ErrorLog 出现 'Lock wait timeout exceeded' 错误，频次 120/min",
        "confidence": 0.88
      }
    ]
  },
  "evidence_summary": {
    "total": 15,
    "by_dimension": {
      "change": 2,
      "downstream": 5,
      "cluster": 3,
      "errorlog": 3,
      "upstream": 1,
      "problem": 1
    }
  },
  "suggestions": [
    "回滚 DB 配置变更（innodb_buffer_pool_size）",
    "检查 DB 主从同步状态，必要时重建从库",
    "临时扩容 order-service Pod 缓解超时"
  ],
  "satisfaction_url": "https://deeprca.example.com/feedback?trace_id=trace-20260710-001"
}
```

**处理逻辑**：

```python
def reporter_node(state: DeepRCAState) -> dict:
    """生成分析报告并推送"""
    root_cause = state.get("root_cause", {})
    evidence_summary = state.get("collected_evidence", {}).get("summary", {})

    report = {
        "trace_id": state["trace_id"],
        "alert_id": state["alert"]["alert_id"],
        "service_name": state["alert"]["service_name"],
        "alert_type": state["alert"]["alert_type"],
        "severity": state["alert"]["severity"],
        "analysis_start_time": state["start_time"],
        "analysis_end_time": datetime.now().isoformat(),
        "duration_seconds": _calc_duration(state["start_time"]),
        "root_cause": root_cause,
        "evidence_summary": evidence_summary,
        "suggestions": root_cause.get("suggestions", []),
        "satisfaction_url": _build_feedback_url(state["trace_id"]),
    }

    # 推送通知（IM/邮件/Webhook）
    _push_notification(report)

    # 触发满意度延迟推送（30分钟后）
    _schedule_satisfaction_push(state["trace_id"], delay_minutes=30)

    return {
        "report": json.dumps(report, ensure_ascii=False, indent=2),
        "status": "completed",
    }
```

## 3. Agent System Prompt 设计

```python
COORDINATOR_SYSTEM_PROMPT = """你是一个故障诊断智能体系统的通用分析 Agent（Coordinator Agent）。

你的职责是：
1. 接收告警事件，理解故障上下文
2. 拆解分析任务，覆盖六个维度：变更、上游流量、下游依赖、集群状态、错误日志、已知问题
3. 并发调度领域专家子 Agent 执行下钻分析
4. 汇聚分析结果，构建证据链
5. 生成结构化分析报告

分析原则：
- 优先检查变更维度：90% 的故障由变更引起
- 并发分析：六个维度同时执行，不串行等待
- 证据驱动：每个结论必须有工具调用结果作为证据
- 置信度量化：所有发现需标注置信度（0.0~1.0）
- 容错优先：单个维度失败不影响整体分析

输出要求：
- 根因结论需包含：结论 + 置信度 + 证据链
- 建议措施需可执行、有优先级
- 报告格式遵循 JSON Schema

当前告警信息：
- 服务: {service_name}
- 告警类型: {alert_type}
- 严重程度: {severity}
- 描述: {description}
- 时间: {timestamp}
"""
```

## 4. 工具接口定义

### 4.1 query_metrics

```python
from langchain_core.tools import tool

@tool
def query_metrics(
    service_name: str,
    metric_name: str,
    start_time: str,
    end_time: str,
    granularity: str = "1m",
    labels: dict = None,
) -> dict:
    """查询服务指标时序数据

    Args:
        service_name: 服务名称 (如 "order-service")
        metric_name: 指标名称 (如 "qps", "tp99", "error_rate", "cpu_usage", "memory_usage")
        start_time: 开始时间 ISO8601 格式
        end_time: 结束时间 ISO8601 格式
        granularity: 粒度 (1m/5m/1h)
        labels: 标签过滤 (cluster, env, pod 等)

    Returns:
        {
            "metric": "qps",
            "service": "order-service",
            "data_points": [
                {"timestamp": "2026-07-10T14:00:00Z", "value": 1200.0},
                {"timestamp": "2026-07-10T14:01:00Z", "value": 1350.0},
                ...
            ],
            "aggregation": {"min": 800, "max": 1500, "avg": 1100, "p99": 1450}
        }
    """
    # 实际实现调用监控平台 API 或验证模拟环境
    ...
```

### 4.2 query_error_logs

```python
@tool
def query_error_logs(
    service_name: str,
    start_time: str,
    end_time: str,
    keyword: str = None,
    level: str = "ERROR",
    limit: int = 100,
) -> dict:
    """查询服务错误日志

    Args:
        service_name: 服务名称
        start_time: 开始时间 ISO8601
        end_time: 结束时间 ISO8601
        keyword: 关键词过滤 (如 "timeout", "NullPointer", "Lock wait")
        level: 日志级别 (ERROR/FATAL/WARN)
        limit: 返回条数上限

    Returns:
        {
            "total": 156,
            "logs": [
                {
                    "timestamp": "2026-07-10T14:25:32Z",
                    "level": "ERROR",
                    "message": "Lock wait timeout exceeded; try restarting transaction",
                    "stack_trace": "...",
                    "pod": "order-service-7d4f6b-x2k9l",
                },
                ...
            ],
            "error_patterns": [
                {"pattern": "Lock wait timeout", "count": 120, "first_seen": "14:23:01"},
                {"pattern": "Connection refused", "count": 30, "first_seen": "14:28:15"},
            ]
        }
    """
    ...
```

### 4.3 query_recent_changes

```python
@tool
def query_recent_changes(
    service_name: str,
    time_range: str = "24h",
    change_type: str = None,
) -> dict:
    """查询服务最近变更记录

    Args:
        service_name: 服务名称
        time_range: 时间范围 (1h/6h/24h/7d)
        change_type: 变更类型 (deploy/config/scale/rollback)

    Returns:
        {
            "total": 3,
            "changes": [
                {
                    "change_id": "chg-001",
                    "type": "config",
                    "description": "调整 DB 连接池 innodb_buffer_pool_size 从 4G 到 8G",
                    "operator": "zhangsan",
                    "timestamp": "2026-07-10T14:25:00Z",
                    "risk_level": "medium",
                    "related_service": "mysql-prod-01",
                },
                {
                    "change_id": "chg-002",
                    "type": "deploy",
                    "description": "order-service v2.3.1 部署",
                    "operator": "lisi",
                    "timestamp": "2026-07-10T10:00:00Z",
                    "risk_level": "low",
                    "related_service": "order-service",
                },
            ]
        }
    """
    ...
```

### 4.4 query_trace

```python
@tool
def query_trace(
    service_name: str,
    start_time: str,
    end_time: str,
    trace_id: str = None,
    status: str = None,
    limit: int = 50,
) -> dict:
    """查询分布式调用链路

    Args:
        service_name: 服务名称
        start_time: 开始时间
        end_time: 结束时间
        trace_id: 指定 trace ID（可选）
        status: 链路状态过滤 (success/error/timeout)
        limit: 返回条数上限

    Returns:
        {
            "total": 42,
            "traces": [
                {
                    "trace_id": "trace-abc-123",
                    "status": "timeout",
                    "duration_ms": 850,
                    "spans": [
                        {"service": "order-service", "operation": "createOrder", "duration_ms": 850, "status": "timeout"},
                        {"service": "mysql-prod-01", "operation": "SELECT * FROM orders", "duration_ms": 800, "status": "timeout"},
                    ],
                    "timestamp": "2026-07-10T14:28:00Z",
                },
                ...
            ],
            "slow_spans": [
                {"service": "mysql-prod-01", "avg_duration_ms": 780, "p99_duration_ms": 950},
            ]
        }
    """
    ...
```

### 4.5 query_related_alerts

```python
@tool
def query_related_alerts(
    service_name: str,
    time_range: str = "7d",
    alert_type: str = None,
) -> dict:
    """查询关联告警和已知问题

    Args:
        service_name: 服务名称
        time_range: 时间范围
        alert_type: 告警类型过滤

    Returns:
        {
            "related_alerts": [
                {
                    "alert_id": "alert-20260708-003",
                    "service_name": "order-service",
                    "alert_type": "timeout",
                    "timestamp": "2026-07-08T10:00:00Z",
                    "resolution": "DB 主从延迟，重建从库后恢复",
                },
            ],
            "known_issues": [
                {
                    "issue_id": "KI-001",
                    "title": "order-service TP99 突增",
                    "pattern": "DB slave delay > 10s 时触发",
                    "solution": "检查 DB 主从同步，必要时重建从库",
                    "last_occurrence": "2026-07-08",
                },
            ]
        }
    """
    ...
```

### 4.6 query_topology

```python
@tool
def query_topology(
    service_name: str,
    depth: int = 2,
    direction: str = "both",
) -> dict:
    """查询服务拓扑关系

    Args:
        service_name: 服务名称
        depth: 拓扑深度
        direction: 方向 (upstream/downstream/both)

    Returns:
        {
            "service": "order-service",
            "upstream": [
                {"service": "api-gateway", "call_type": "HTTP", "qps": 1200, "error_rate": 0.01},
                {"service": "scheduler", "call_type": "HTTP", "qps": 300, "error_rate": 0.0},
            ],
            "downstream": [
                {"service": "mysql-prod-01", "call_type": "JDBC", "qps": 1500, "error_rate": 0.05, "avg_rt_ms": 120},
                {"service": "redis-cluster-01", "call_type": "Redis", "qps": 3000, "error_rate": 0.0, "avg_rt_ms": 2},
                {"service": "kafka-cluster-01", "call_type": "Kafka", "qps": 800, "error_rate": 0.0, "avg_rt_ms": 5},
                {"service": "payment-service", "call_type": "RPC", "qps": 600, "error_rate": 0.02, "avg_rt_ms": 80},
            ],
        }
    """
    ...
```

## 5. LangGraph 图定义

```python
from langgraph.graph import StateGraph, END
from deeprca.graph.state import DeepRCAState

def build_coordinator_graph() -> StateGraph:
    """构建通用分析 Agent 的主编排图"""
    graph = StateGraph(DeepRCAState)

    # 添加节点
    graph.add_node("intake", intake_node)
    graph.add_node("planner", planner_node)
    graph.add_node("dispatcher", dispatcher_node)
    graph.add_node("collector", collector_node)
    graph.add_node("root_cause", root_cause_node)
    graph.add_node("reporter", reporter_node)

    # 定义边
    graph.set_entry_point("intake")
    graph.add_edge("intake", "planner")
    graph.add_edge("planner", "dispatcher")
    graph.add_edge("dispatcher", "collector")
    graph.add_edge("collector", "root_cause")
    graph.add_edge("root_cause", "reporter")
    graph.add_edge("reporter", END)

    # 条件边：超时强制进入根因定位
    graph.add_conditional_edges(
        "dispatcher",
        _check_timeout,
        {
            "normal": "collector",
            "timeout": "root_cause",
        }
    )

    return graph.compile()

def _check_timeout(state: DeepRCAState) -> str:
    """检查是否超时"""
    start = datetime.fromisoformat(state["start_time"])
    elapsed = (datetime.now() - start).total_seconds()
    if elapsed > 120:
        return "timeout"
    return "normal"
```

## 6. 验证 API 端点

### 6.1 提交分析请求

```
POST /api/v1/analyze
Content-Type: application/json

Request:
{
  "alert_id": "alert-20260710-001",
  "service_name": "order-service",
  "alert_type": "timeout",
  "severity": "P1",
  "timestamp": "2026-07-10T14:30:00+08:00",
  "description": "order-service TP99 延迟突增至 800ms",
  "labels": {
    "cluster": "prod-cluster-01",
    "env": "production"
  }
}

Response 202:
{
  "trace_id": "trace-20260710-001",
  "status": "running",
  "websocket_url": "ws://localhost:8000/api/v1/analyze/trace-20260710-001/stream"
}
```

### 6.2 查询分析状态

```
GET /api/v1/analyze/{trace_id}/status

Response 200:
{
  "trace_id": "trace-20260710-001",
  "status": "running",
  "current_node": "dispatcher",
  "progress": {
    "total_dimensions": 6,
    "completed": 3,
    "failed": 0,
    "pending": 3
  },
  "elapsed_seconds": 15
}
```

### 6.3 获取分析结果

```
GET /api/v1/analyze/{trace_id}/result

Response 200:
{
  "trace_id": "trace-20260710-001",
  "status": "completed",
  "report": { ... }
}
```

### 6.4 WebSocket 实时推送

```
WS /api/v1/analyze/{trace_id}/stream

Events:
{
  "event": "node_started",
  "node": "dispatcher",
  "timestamp": "2026-07-10T14:30:10Z"
}
{
  "event": "sub_agent_completed",
  "dimension": "downstream",
  "confidence": 0.85,
  "findings_count": 3,
  "timestamp": "2026-07-10T14:30:25Z"
}
{
  "event": "analysis_completed",
  "trace_id": "trace-20260710-001",
  "duration_seconds": 40,
  "timestamp": "2026-07-10T14:30:45Z"
}
```

### 6.5 满意度反馈

```
POST /api/v1/feedback
Content-Type: application/json

Request:
{
  "trace_id": "trace-20260710-001",
  "satisfaction": "satisfied",
  "root_cause_correct": true,
  "useful_evidence": ["downstream", "errorlog"],
  "comment": "根因定位准确，建议措施有效"
}

Response 200:
{
  "status": "received",
  "trace_id": "trace-20260710-001"
}
```

## 7. 异常处理与降级

| 场景 | 处理策略 |
|------|----------|
| 告警格式异常 | 返回 400，提示必需字段缺失 |
| 监控平台不可达 | 降级：仅基于日志和变更分析 |
| 所有子 Agent 超时 | 降级：基于告警描述生成初步报告，标记为低置信度 |
| LLM 调用失败 | 降级：使用规则模板生成报告 |
| 分析超时（>120s） | 强制进入 root_cause，基于已有证据输出 |

## 8. 测试要点

- Intake 节点：验证各种告警类型的字段解析和时间窗口推导
- Planner 节点：验证告警类型到维度的映射正确性
- Dispatcher 节点：验证并发执行、超时处理、容错降级
- Collector 节点：验证证据池的排序和去重
- Reporter 节点：验证报告 JSON 结构完整性和满意度 URL 生成
- API 端点：验证 5 个端点的请求响应格式
- WebSocket：验证事件推送的实时性和完整性
