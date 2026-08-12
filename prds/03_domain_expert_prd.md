# DeepRCA-Agent 领域专家子 Agent PRD

| 字段 | 内容 |
|------|------|
| 文档版本 | v1.0 |
| 创建日期 | 2026-07-10 |
| 状态 | Draft |
| 负责人 | - |
| 关联文档 | 01_overview_prd.md, 02_general_analyzer_prd.md |

## 1. 概述

领域专家子 Agent 是 DeepRCA-Agent 系统的第二层 Agent，每个子 Agent 封装特定技术领域的专业知识和分析逻辑。通用分析 Agent 的 Dispatcher 节点通过 LangGraph 子图机制并发调度这些领域专家，实现六维度的下钻分析。

本文档定义四个领域专家子 Agent：DB Expert、Redis Expert、Mafka Expert、RPC Expert，以及两个通用分析维度 Agent：变更分析 Agent 和错误日志分析 Agent。

## 2. 子 Agent 架构

### 2.1 通用子 Agent 基类

所有领域专家子 Agent 继承统一的基类，遵循相同的生命周期和接口约定：

```python
from abc import ABC, abstractmethod
from langgraph.graph import StateGraph, END
from deeprca.graph.state import DeepRCAState, SubAgentResult

class BaseExpertAgent(ABC):
    """领域专家子 Agent 基类"""

    def __init__(self, name: str, dimension: str):
        self.name = name
        self.dimension = dimension
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """构建子 Agent 的 LangGraph 子图"""
        graph = StateGraph(DeepRCAState)
        graph.add_node("collect", self._collect_node)
        graph.add_node("analyze", self._analyze_node)
        graph.add_node("conclude", self._conclude_node)
        graph.set_entry_point("collect")
        graph.add_edge("collect", "analyze")
        graph.add_edge("analyze", "conclude")
        graph.add_edge("conclude", END)
        return graph.compile()

    @abstractmethod
    async def _collect_node(self, state: DeepRCAState) -> dict:
        """数据采集节点：调用领域特定工具获取数据"""
        ...

    @abstractmethod
    async def _analyze_node(self, state: DeepRCAState) -> dict:
        """分析节点：对采集的数据执行领域特定分析逻辑"""
        ...

    async def _conclude_node(self, state: DeepRCAState) -> dict:
        """结论节点：生成 SubAgentResult"""
        findings = state.get("agent_findings", [])
        evidence = state.get("agent_evidence", [])
        confidence = self._aggregate_confidence(findings)

        result = SubAgentResult(
            agent_name=self.name,
            dimension=self.dimension,
            findings=findings,
            confidence=confidence,
            evidence=evidence,
            timestamp=datetime.now().isoformat(),
        )
        return {"sub_agent_results": [result]}

    def _aggregate_confidence(self, findings: List[dict]) -> float:
        """聚合多个发现的置信度"""
        if not findings:
            return 0.0
        weights = [f.get("confidence", 0.0) * f.get("weight", 1.0) for f in findings]
        total_weight = sum(f.get("weight", 1.0) for f in findings)
        return sum(weights) / total_weight if total_weight > 0 else 0.0
```

### 2.2 子 Agent 注册与调度

```python
EXPERT_AGENT_REGISTRY = {
    "db": DBExpertAgent,
    "redis": RedisExpertAgent,
    "mafka": MafkaExpertAgent,
    "rpc": RPCEXpertAgent,
    "change": ChangeAnalysisAgent,
    "errorlog": ErrorLogAnalysisAgent,
}

def get_expert_agent(domain: str) -> BaseExpertAgent:
    """根据领域获取对应的专家 Agent 实例"""
    agent_cls = EXPERT_AGENT_REGISTRY.get(domain)
    if not agent_cls:
        raise ValueError(f"未知的领域专家: {domain}")
    return agent_cls()
```

## 3. DB Expert Agent

### 3.1 职责

分析数据库层面的异常，包括慢查询、连接池耗尽、锁等待、主从延迟、磁盘 IO 瓶颈等。

### 3.2 分析维度

| 检查项 | 检测方法 | 异常阈值 | 置信度权重 |
|--------|----------|----------|------------|
| 慢查询突增 | 查询慢日志数量 + P99 耗时 | 慢查询数 > 基线 3 倍 | 0.8 |
| 连接池耗尽 | 查询活跃连接数 / 最大连接数 | 使用率 > 80% | 0.9 |
| 锁等待 | 查询 innodb_lock_waits | 等待数 > 5 | 0.85 |
| 主从延迟 | 查询 slave_delay 指标 | 延迟 > 5s | 0.9 |
| 磁盘 IO | 查询 iops / io_wait | io_wait > 20% | 0.7 |
| 死锁 | 查询死锁日志 | 出现死锁记录 | 0.95 |

### 3.3 工具接口

```python
@tool
def query_db_metrics(
    db_instance: str,
    metric_names: List[str],
    start_time: str,
    end_time: str,
    granularity: str = "1m",
) -> dict:
    """查询数据库指标

    Args:
        db_instance: DB 实例标识 (如 "mysql-prod-01")
        metric_names: 指标列表 (如 ["active_connections", "slow_query_count",
                       "slave_delay_seconds", "innodb_lock_waits", "io_wait_ratio"])
        start_time: 开始时间 ISO8601
        end_time: 结束时间 ISO8601
        granularity: 粒度

    Returns:
        {
            "db_instance": "mysql-prod-01",
            "metrics": {
                "active_connections": {
                    "data_points": [...],
                    "current": 180,
                    "max": 200,
                    "usage_ratio": 0.9,
                },
                "slow_query_count": {
                    "data_points": [...],
                    "current": 45,
                    "baseline_avg": 5,
                    "anomaly_ratio": 9.0,
                },
                "slave_delay_seconds": {
                    "data_points": [...],
                    "current": 15.2,
                    "threshold": 5.0,
                    "exceeded": True,
                },
            }
        }
    """
    ...

@tool
def query_slow_log(
    db_instance: str,
    start_time: str,
    end_time: str,
    threshold_ms: int = 500,
    limit: int = 20,
) -> dict:
    """查询数据库慢查询日志

    Returns:
        {
            "total": 45,
            "slow_queries": [
                {
                    "sql": "SELECT * FROM orders WHERE created_at > ?",
                    "duration_ms": 850,
                    "timestamp": "2026-07-10T14:25:00Z",
                    "rows_examined": 1500000,
                    "rows_returned": 100,
                    "index_used": "idx_created_at",
                },
            ],
            "top_slow": [...],
            "patterns": [
                {"pattern": "SELECT * FROM orders", "count": 30, "avg_duration_ms": 780},
            ],
        }
    """
    ...

@tool
def query_db_topology(
    db_instance: str,
) -> dict:
    """查询数据库拓扑（主从关系、读写分离配置）

    Returns:
        {
            "instance": "mysql-prod-01",
            "role": "master",
            "slaves": [
                {"instance": "mysql-prod-01-slave-01", "delay_seconds": 15.2, "status": "lagging"},
                {"instance": "mysql-prod-01-slave-02", "delay_seconds": 0.5, "status": "normal"},
            ],
            "connection_pool": {
                "max": 200,
                "active": 180,
                "idle": 20,
                "waiting": 5,
            },
        }
    """
    ...
```

### 3.4 System Prompt

```python
DB_EXPERT_PROMPT = """你是数据库故障诊断专家 Agent。

你的分析范围：
- MySQL / PostgreSQL 数据库异常
- 慢查询、连接池、锁等待、主从延迟
- 磁盘 IO 瓶颈、死锁

分析逻辑：
1. 首先检查主从延迟，延迟过大常导致读超时
2. 检查连接池使用率，超过 80% 可能导致连接等待
3. 检查慢查询突增，对比基线
4. 检查锁等待和死锁
5. 检查磁盘 IO 是否瓶颈

置信度规则：
- 主从延迟 > 10s 且告警为 timeout: confidence = 0.9
- 连接池使用率 > 90%: confidence = 0.85
- 死锁记录出现: confidence = 0.95
- 多个指标同时异常: 置信度叠加，最高 0.95
"""
```

### 3.5 子图定义

```python
class DBExpertAgent(BaseExpertAgent):
    def __init__(self):
        super().__init__(name="db_expert", dimension="downstream")

    async def _collect_node(self, state: DeepRCAState) -> dict:
        """采集 DB 相关数据"""
        alert = state["alert"]
        db_instances = self._extract_db_instances(alert)

        results = await asyncio.gather(*[
            query_db_metrics.ainvoke({
                "db_instance": inst,
                "metric_names": ["active_connections", "slow_query_count",
                                "slave_delay_seconds", "innodb_lock_waits", "io_wait_ratio"],
                "start_time": alert["timestamp"],
                "end_time": datetime.now().isoformat(),
            }) for inst in db_instances
        ], return_exceptions=True)

        return {"db_metrics": results}

    async def _analyze_node(self, state: DeepRCAState) -> dict:
        """分析 DB 指标，检测异常"""
        findings = []
        evidence = []
        metrics = state.get("db_metrics", [])

        for m in metrics:
            if isinstance(m, Exception):
                evidence.append(f"DB 指标查询失败: {str(m)}")
                continue

            # 检查主从延迟
            slave_delay = m.get("metrics", {}).get("slave_delay_seconds", {})
            if slave_delay.get("exceeded"):
                findings.append({
                    "description": f"DB {m['db_instance']} 主从延迟 {slave_delay['current']}s，超过阈值 {slave_delay['threshold']}s",
                    "confidence": 0.9,
                    "weight": 2.0,
                    "category": "slave_delay",
                })
                evidence.append(f"slave_delay={slave_delay['current']}s (threshold={slave_delay['threshold']}s)")

            # 检查连接池
            conn = m.get("metrics", {}).get("active_connections", {})
            if conn.get("usage_ratio", 0) > 0.8:
                findings.append({
                    "description": f"DB {m['db_instance']} 连接池使用率 {conn['usage_ratio']*100:.1f}%",
                    "confidence": 0.85,
                    "weight": 1.5,
                    "category": "connection_pool",
                })

            # 检查慢查询
            slow = m.get("metrics", {}).get("slow_query_count", {})
            if slow.get("anomaly_ratio", 1.0) > 3.0:
                findings.append({
                    "description": f"DB {m['db_instance']} 慢查询数 {slow['current']}，基线 {slow['baseline_avg']}，突增 {slow['anomaly_ratio']}倍",
                    "confidence": 0.8,
                    "weight": 1.0,
                    "category": "slow_query",
                })

        return {"agent_findings": findings, "agent_evidence": evidence}
```

## 4. Redis Expert Agent

### 4.1 职责

分析 Redis 缓存层面异常，包括内存使用、热点 Key、大 Key、命中率下降、连接异常等。

### 4.2 分析维度

| 检查项 | 检测方法 | 异常阈值 | 置信度权重 |
|--------|----------|----------|------------|
| 内存使用 | 查询 used_memory / maxmemory | 使用率 > 85% | 0.8 |
| 命中率下降 | 查询 hit_rate 指标 | 下降 > 10pp | 0.85 |
| 热点 Key | 查询 hotkey 统计 | QPS > 10000 | 0.75 |
| 大 Key | 查询 bigkey 扫描 | size > 10MB | 0.8 |
| 连接数 | 查询 connected_clients | > maxclients * 80% | 0.9 |
| 慢日志 | 查询 slowlog | 执行时间 > 10ms | 0.7 |

### 4.3 工具接口

```python
@tool
def query_redis_metrics(
    redis_instance: str,
    metric_names: List[str],
    start_time: str,
    end_time: str,
    granularity: str = "1m",
) -> dict:
    """查询 Redis 指标

    metric_names 可选: ["used_memory", "maxmemory", "hit_rate",
                       "connected_clients", "maxclients", "ops_per_sec",
                       "evicted_keys", "expired_keys"]
    """
    ...

@tool
def query_hotkey(
    redis_instance: str,
    top_n: int = 10,
) -> dict:
    """查询 Redis 热点 Key

    Returns:
        {
            "hotkeys": [
                {"key": "user:profile:12345", "qps": 15000, "type": "string", "size": "1KB"},
                {"key": "order:cache:batch", "qps": 12000, "type": "hash", "size": "5KB"},
            ]
        }
    """
    ...

@tool
def query_redis_topology(
    redis_instance: str,
) -> dict:
    """查询 Redis 集群拓扑

    Returns:
        {
            "instance": "redis-cluster-01",
            "mode": "cluster",
            "nodes": [
                {"id": "node-01", "role": "master", "slots": "0-5460", "memory_used": "4.2GB", "memory_max": "8GB"},
                {"id": "node-02", "role": "slave", "master": "node-01", "memory_used": "4.1GB"},
            ],
            "connection_pool": {"active": 150, "max": 200, "waiting": 0},
        }
    """
    ...
```

### 4.4 System Prompt

```python
REDIS_EXPERT_PROMPT = """你是 Redis 缓存故障诊断专家 Agent。

你的分析范围：
- Redis 内存使用、热点 Key、大 Key
- 命中率下降、连接异常、慢日志

分析逻辑：
1. 首先检查内存使用率，超过 85% 可能触发 eviction
2. 检查命中率是否突然下降，下降可能意味着缓存策略变更或数据模式变化
3. 检查热点 Key，高 QPS Key 可能导致单节点瓶颈
4. 检查大 Key，超过 10MB 的 Key 可能阻塞 Redis
5. 检查连接数是否接近上限

置信度规则：
- 内存使用率 > 90% + eviction > 0: confidence = 0.85
- 命中率下降 > 20pp: confidence = 0.9
- 大 Key > 50MB: confidence = 0.9
"""
```

## 5. Mafka (Kafka) Expert Agent

### 5.1 职责

分析 Kafka 消息队列异常，包括消费延迟、消息积压、生产消费速率异常、Rebalance 频繁等。

### 5.2 分析维度

| 检查项 | 检测方法 | 异常阈值 | 置信度权重 |
|--------|----------|----------|------------|
| 消费延迟 | 查询 consumer_lag 指标 | lag > 10000 | 0.85 |
| 消息积压 | 查询 backlog 趋势 | 积压持续增长 | 0.8 |
| 消费速率下降 | 对比生产/消费 QPS | 消费速率 < 生产的 50% | 0.9 |
| Rebalance 频繁 | 查询 rebalance 事件 | 5分钟内 > 3 次 | 0.85 |
| 分区不均 | 查询分区消费分布 | 最慢分区 lag > 平均 3 倍 | 0.7 |
| 消费者离线 | 查询消费者心跳 | 心跳超时 | 0.95 |

### 5.3 工具接口

```python
@tool
def query_mafka_metrics(
    cluster: str,
    topic: str,
    consumer_group: str,
    metric_names: List[str],
    start_time: str,
    end_time: str,
) -> dict:
    """查询 Kafka/Mafka 指标

    metric_names 可选: ["consumer_lag", "consume_rate", "produce_rate",
                       "partition_count", "rebalance_count", "consumer_heartbeat"]
    """
    ...

@tool
def query_consumer_lag(
    cluster: str,
    topic: str,
    consumer_group: str,
    top_n: int = 10,
) -> dict:
    """查询消费者积压详情

    Returns:
        {
            "cluster": "kafka-prod-01",
            "topic": "order-events",
            "consumer_group": "order-consumer-group",
            "total_lag": 50000,
            "partitions": [
                {"partition": 0, "lag": 5000, "consumer": "consumer-1", "last_poll": "2026-07-10T14:29:00Z"},
                {"partition": 1, "lag": 30000, "consumer": None, "last_poll": "2026-07-10T14:20:00Z"},
                {"partition": 2, "lag": 15000, "consumer": "consumer-2", "last_poll": "2026-07-10T14:28:30Z"},
            ],
            "rebalance_events": [
                {"timestamp": "2026-07-10T14:25:00Z", "reason": "consumer-3 left group"},
            ],
        }
    """
    ...
```

### 5.4 System Prompt

```python
MAFKA_EXPERT_PROMPT = """你是 Kafka/Mafka 消息队列故障诊断专家 Agent。

你的分析范围：
- 消费延迟、消息积压
- 生产消费速率不匹配
- Rebalance 频繁、消费者离线
- 分区消费不均

分析逻辑：
1. 首先检查消费者是否有离线（心跳超时），离线直接导致积压
2. 检查消费 lag 趋势，持续增长说明消费能力不足
3. 对比生产/消费速率，消费速率远低于生产速率说明处理瓶颈
4. 检查 Rebalance 频率，频繁 Rebalance 会导致消费中断
5. 检查分区级别 lag 分布，不均说明消费者分配有问题

置信度规则：
- 消费者离线 + 积压增长: confidence = 0.95
- 消费速率 < 生产速率 50%: confidence = 0.9
- 5分钟内 Rebalance > 3 次: confidence = 0.85
"""
```

## 6. RPC Expert Agent

### 6.1 职责

分析 RPC 调用链路异常，包括调用失败率、RT 突变、依赖拓扑异常、熔断/降级触发等。

### 6.2 分析维度

| 检查项 | 检测方法 | 异常阈值 | 置信度权重 |
|--------|----------|----------|------------|
| 调用失败率 | 查询 error_rate 指标 | > 5% | 0.9 |
| RT 突变 | 四分位检测 TP99 | 突增 > 基线 3 倍 | 0.85 |
| 依赖拓扑变化 | 对比拓扑快照 | 新增/移除依赖节点 | 0.7 |
| 熔断触发 | 查询 circuit_breaker 事件 | 触发记录 | 0.9 |
| 调用链断裂 | 查询 trace 中断 | span 缺失 | 0.8 |
| 超时级联 | 分析 trace 超时传播 | 多级超时 | 0.85 |

### 6.3 工具接口

```python
@tool
def query_rpc_metrics(
    service_name: str,
    endpoint: str,
    metric_names: List[str],
    start_time: str,
    end_time: str,
) -> dict:
    """查询 RPC 调用指标

    metric_names 可选: ["call_count", "error_count", "error_rate",
                       "avg_rt_ms", "p99_rt_ms", "p95_rt_ms",
                       "circuit_breaker_count"]
    """
    ...

@tool
def query_dependency_graph(
    service_name: str,
    depth: int = 2,
) -> dict:
    """查询服务依赖图谱

    Returns:
        {
            "service": "order-service",
            "dependencies": [
                {
                    "service": "mysql-prod-01",
                    "type": "DB",
                    "call_count": 1500,
                    "error_rate": 0.05,
                    "avg_rt_ms": 120,
                    "p99_rt_ms": 850,
                    "circuit_breaker": {"status": "closed", "trigger_count": 0},
                },
                {
                    "service": "payment-service",
                    "type": "RPC",
                    "call_count": 600,
                    "error_rate": 0.02,
                    "avg_rt_ms": 80,
                    "p99_rt_ms": 200,
                },
            ],
            "dependents": [
                {"service": "api-gateway", "call_count": 1200, "error_rate": 0.01},
            ],
        }
    """
    ...
```

### 6.4 System Prompt

```python
RPC_EXPERT_PROMPT = """你是 RPC 调用链路故障诊断专家 Agent。

你的分析范围：
- RPC 调用失败率、RT 突变
- 依赖拓扑变化、熔断/降级
- 调用链断裂、超时级联

分析逻辑：
1. 检查调用失败率是否突增，超过 5% 需重点关注
2. 用四分位算法检测 TP99 是否突变，突变说明性能退化
3. 检查依赖拓扑是否有变化，新增/移除依赖可能是变更导致
4. 检查熔断器是否触发，触发说明下游异常已达到阈值
5. 分析调用链是否存在超时级联（上游超时由下游引起）

置信度规则：
- 失败率 > 10% + 熔断触发: confidence = 0.95
- TP99 突增 > 5 倍: confidence = 0.85
- 依赖拓扑新增节点 + 时间吻合变更: confidence = 0.8
"""
```

## 7. 变更分析 Agent

### 7.1 职责

检查最近变更记录，关联告警时间窗口，判断变更是否为故障根因。

### 7.2 分析逻辑

```python
class ChangeAnalysisAgent(BaseExpertAgent):
    def __init__(self):
        super().__init__(name="change_agent", dimension="change")

    async def _collect_node(self, state: DeepRCAState) -> dict:
        alert = state["alert"]
        # 查询 24h 内的变更记录
        changes = await query_recent_changes.ainvoke({
            "service_name": alert["service_name"],
            "time_range": "24h",
        })
        return {"change_records": changes}

    async def _analyze_node(self, state: DeepRCAState) -> dict:
        findings = []
        evidence = []
        alert_time = datetime.fromisoformat(state["alert"]["timestamp"])
        changes = state.get("change_records", {}).get("changes", [])

        for change in changes:
            change_time = datetime.fromisoformat(change["timestamp"])
            time_diff = abs((alert_time - change_time).total_seconds()) / 3600  # 小时差

            # 变更时间与告警时间吻合度
            if time_diff < 0.5:  # 30 分钟内
                confidence = 0.9
                evidence.append(f"变更 {change['change_id']} 在告警前 {time_diff*60:.0f} 分钟执行")
            elif time_diff < 2:
                confidence = 0.7
                evidence.append(f"变更 {change['change_id']} 在告警前 {time_diff:.1f} 小时执行")
            else:
                continue  # 超过 2 小时的变更不太可能是直接原因

            # 变更风险等级加成
            if change.get("risk_level") == "high":
                confidence = min(confidence + 0.05, 0.95)

            findings.append({
                "description": f"变更: {change['description']} (操作人: {change['operator']}, 时间: {change['timestamp']})",
                "confidence": confidence,
                "weight": 2.0 if time_diff < 0.5 else 1.0,
                "category": "change",
                "metadata": change,
            })

        return {"agent_findings": findings, "agent_evidence": evidence}
```

## 8. 错误日志分析 Agent

### 8.1 职责

扫描 ErrorLog，提取异常堆栈和错误模式，关联告警时间窗口。

### 8.2 分析逻辑

```python
class ErrorLogAnalysisAgent(BaseExpertAgent):
    def __init__(self):
        super().__init__(name="errorlog_agent", dimension="errorlog")

    async def _collect_node(self, state: DeepRCAState) -> dict:
        alert = state["alert"]
        logs = await query_error_logs.ainvoke({
            "service_name": alert["service_name"],
            "start_time": alert["timestamp"],
            "end_time": datetime.now().isoformat(),
            "level": "ERROR",
            "limit": 200,
        })
        return {"error_logs": logs}

    async def _analyze_node(self, state: DeepRCAState) -> dict:
        findings = []
        evidence = []
        logs = state.get("error_logs", {})
        error_patterns = logs.get("error_patterns", [])

        for pattern in error_patterns:
            count = pattern.get("count", 0)
            first_seen = pattern.get("first_seen", "")

            # 高频错误模式
            if count > 50:
                confidence = 0.85
                evidence.append(f"错误模式 '{pattern['pattern']}' 出现 {count} 次，首次出现 {first_seen}")
            elif count > 10:
                confidence = 0.7
                evidence.append(f"错误模式 '{pattern['pattern']}' 出现 {count} 次")
            else:
                confidence = 0.5

            findings.append({
                "description": f"错误日志: {pattern['pattern']} (频次: {count}/min)",
                "confidence": confidence,
                "weight": 1.5 if count > 50 else 1.0,
                "category": "error_log",
                "metadata": pattern,
            })

        # 检查特定错误模式
        for log in logs.get("logs", [])[:10]:
            stack = log.get("stack_trace", "")
            if "Lock wait timeout" in stack:
                findings.append({
                    "description": "检测到数据库锁等待超时: Lock wait timeout exceeded",
                    "confidence": 0.9,
                    "weight": 2.0,
                    "category": "db_lock",
                })
            elif "Connection refused" in stack:
                findings.append({
                    "description": "检测到连接拒绝: Connection refused",
                    "confidence": 0.85,
                    "weight": 2.0,
                    "category": "connection",
                })
            elif "OutOfMemoryError" in stack:
                findings.append({
                    "description": "检测到内存溢出: OutOfMemoryError",
                    "confidence": 0.95,
                    "weight": 2.5,
                    "category": "oom",
                })

        return {"agent_findings": findings, "agent_evidence": evidence}
```

## 9. 子 Agent 并发调度集成

```python
async def dispatch_to_experts(
    task_plan: List[dict],
    state: DeepRCAState
) -> List[SubAgentResult]:
    """并发调度所有领域专家子 Agent"""
    tasks = []

    for task in task_plan:
        dimension = task["dimension"]
        # 映射到领域专家
        domain_map = {
            "change": "change",
            "upstream": "rpc",
            "downstream": ["db", "redis", "mafka", "rpc"],  # 下游拆分为多个领域
            "cluster": "rpc",  # 集群状态复用 RPC 工具
            "errorlog": "errorlog",
            "problem": None,  # 已知问题匹配不需要子 Agent
        }

        domains = domain_map.get(dimension)
        if domains is None:
            continue
        if isinstance(domains, str):
            domains = [domains]

        for domain in domains:
            agent = get_expert_agent(domain)
            tasks.append(agent.graph.ainvoke(state))

    # 并发执行
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 合并结果
    all_results = []
    for result in results:
        if isinstance(result, Exception):
            all_results.append(SubAgentResult(
                agent_name="unknown",
                dimension="unknown",
                findings=[],
                confidence=0.0,
                evidence=[f"子 Agent 执行失败: {str(result)}"],
                timestamp=datetime.now().isoformat(),
            ))
        elif isinstance(result, dict) and "sub_agent_results" in result:
            all_results.extend(result["sub_agent_results"])

    return all_results
```

## 10. 测试要点

| 子 Agent | 测试场景 | 验证点 |
|----------|----------|--------|
| DB Expert | 主从延迟 + 慢查询突增 | 置信度 ≥ 0.85，证据链包含 slave_delay |
| Redis Expert | 内存使用率 90% + eviction | 置信度 ≥ 0.8，发现内存异常 |
| Mafka Expert | 消费者离线 + 积压增长 | 置信度 ≥ 0.9，发现消费者离线 |
| RPC Expert | 失败率 10% + 熔断触发 | 置信度 ≥ 0.9，发现熔断事件 |
| Change Agent | 变更在告警前 20 分钟 | 置信度 ≥ 0.9，时间吻合度高 |
| ErrorLog Agent | OOM 错误 100 次/min | 置信度 ≥ 0.95，检测到 OutOfMemoryError |
| 并发调度 | 6 维度同时执行 | 总耗时 ≤ 30s，部分失败不影响整体 |
