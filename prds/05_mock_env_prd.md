# DeepRCA-Agent 验证接口与模拟环境 PRD

| 字段 | 内容 |
|------|------|
| 文档版本 | v1.0 |
| 创建日期 | 2026-07-10 |
| 状态 | Draft |
| 负责人 | - |
| 关联文档 | 01_overview_prd.md, 02_general_analyzer_prd.md, 03_domain_expert_prd.md, 04_root_cause_prd.md |

## 1. 概述

本文档定义 DeepRCA-Agent 系统的验证接口与模拟环境。模拟环境提供完整的微服务系统模拟，包括 K8s 集群模拟器、多个中间件模拟器（DB/Redis/Kafka）和微服务调用链模拟器，用于端到端验证故障诊断 Agent 的全链路分析和根因定位能力。

模拟环境支持故障注入、指标生成、日志生成和调用链生成，使故障诊断 Agent 能够在无真实生产环境的情况下进行完整的功能验证和性能压测。

## 2. 模拟环境架构

```
┌─────────────────────────────────────────────────────────────┐
│                    模拟环境管理器                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────────┐  │
│  │ 故障注入器 │  │ 场景管理器 │  │ 数据生成器（指标/日志/链路）│  │
│  └────┬─────┘  └────┬─────┘  └───────────┬──────────────┘  │
│       └──────────────┴────────────────────┘                  │
├──────────────────────────────────────────────────────────────┤
│                      模拟组件层                               │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐ │
│  │ K8s 模拟器  │ │ 微服务模拟器│ │ 中间件模拟器│ │ 告警模拟器│ │
│  │            │ │            │ │            │ │          │ │
│  │ Pod/Service│ │ 服务拓扑   │ │ DB/Redis/  │ │ 告警事件  │ │
│  │ Deployment │ │ 调用链     │ │ Kafka      │ │ 生成     │ │
│  └────────────┘ └────────────┘ └────────────┘ └──────────┘ │
├──────────────────────────────────────────────────────────────┤
│                      API 网关层                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  FastAPI 统一 API (RESTful + WebSocket)               │   │
│  │  /api/v1/mock/*                                       │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

## 3. K8s 模拟器

### 3.1 职责

模拟 Kubernetes 集群的 Pod、Service、Deployment 等资源状态，支持 Pod 重启、扩缩容、资源压力等场景。

### 3.2 数据模型

```python
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum

class PodPhase(Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    UNKNOWN = "Unknown"

class PodCondition(Enum):
    READY = "Ready"
    INITIALIZED = "Initialized"
    SCHEDULED = "Scheduled"

@dataclass
class PodStatus:
    name: str
    phase: PodPhase
    conditions: Dict[str, bool]
    containers: List[dict]
    restart_count: int = 0
    cpu_usage: float = 0.0      # 核数
    memory_usage: float = 0.0   # MB
    cpu_limit: float = 2.0
    memory_limit: float = 4096.0
    node_name: str = ""
    start_time: str = ""
    ip: str = ""

@dataclass
class DeploymentStatus:
    name: str
    namespace: str
    replicas_desired: int
    replicas_available: int
    replicas_ready: int
    image: str
    pods: List[PodStatus] = field(default_factory=list)
    creation_timestamp: str = ""

@dataclass
class ServiceStatus:
    name: str
    namespace: str
    type: str          # ClusterIP / NodePort / LoadBalancer
    cluster_ip: str
    ports: List[dict]
    selector: Dict[str, str]
    endpoints: List[str] = field(default_factory=list)

class K8sSimulator:
    """K8s 集群模拟器"""

    def __init__(self):
        self.deployments: Dict[str, DeploymentStatus] = {}
        self.services: Dict[str, ServiceStatus] = {}
        self.pods: Dict[str, PodStatus] = {}
        self.events: List[dict] = []
        self._init_default_cluster()

    def _init_default_cluster(self):
        """初始化默认集群状态"""
        # 创建默认微服务部署
        services = [
            ("order-service", "v2.3.0", 3),
            ("payment-service", "v1.8.0", 2),
            ("user-service", "v3.1.0", 3),
            ("inventory-service", "v2.0.0", 2),
        ]
        for name, image, replicas in services:
            self._create_deployment(name, "default", image, replicas)

    def _create_deployment(self, name, namespace, image, replicas):
        """创建 Deployment"""
        pods = []
        for i in range(replicas):
            pod_name = f"{name}-{hash(name) % 10000}-{i}"
            pod = PodStatus(
                name=pod_name,
                phase=PodPhase.RUNNING,
                conditions={"Ready": True, "Initialized": True, "Scheduled": True},
                containers=[{"name": name, "image": image, "ready": True}],
                restart_count=0,
                cpu_usage=0.5 + i * 0.1,
                memory_usage=1024.0 + i * 100,
                cpu_limit=2.0,
                memory_limit=4096.0,
                node_name=f"node-{i % 3}",
                ip=f"10.244.{i % 3}.{i + 1}",
            )
            pods.append(pod)
            self.pods[pod_name] = pod

        deployment = DeploymentStatus(
            name=name,
            namespace=namespace,
            replicas_desired=replicas,
            replicas_available=replicas,
            replicas_ready=replicas,
            image=image,
            pods=pods,
        )
        self.deployments[name] = deployment

        # 创建对应 Service
        service = ServiceStatus(
            name=name,
            namespace=namespace,
            type="ClusterIP",
            cluster_ip=f"10.96.{hash(name) % 255}.{hash(name) % 255}",
            ports=[{"port": 8080, "targetPort": 8080, "protocol": "TCP"}],
            selector={"app": name},
            endpoints=[pod.ip for pod in pods],
        )
        self.services[name] = service
```

### 3.3 故障注入 API

```python
    def inject_pod_restart(self, deployment_name: str, pod_index: int, reason: str = "OOMKilled"):
        """注入 Pod 重启故障"""
        deployment = self.deployments.get(deployment_name)
        if not deployment or pod_index >= len(deployment.pods):
            return {"error": "deployment or pod not found"}

        pod = deployment.pods[pod_index]
        pod.restart_count += 1
        pod.phase = PodPhase.RUNNING  # 重启后恢复 Running
        pod.start_time = datetime.now().isoformat()

        event = {
            "type": "Warning",
            "reason": reason,
            "message": f"Pod {pod.name} was restarted due to {reason}",
            "timestamp": datetime.now().isoformat(),
            "pod": pod.name,
        }
        self.events.append(event)
        return {"status": "injected", "pod": pod.name, "restart_count": pod.restart_count}

    def inject_resource_pressure(self, deployment_name: str, cpu_usage: float = None, memory_usage: float = None):
        """注入资源压力"""
        deployment = self.deployments.get(deployment_name)
        if not deployment:
            return {"error": "deployment not found"}

        for pod in deployment.pods:
            if cpu_usage is not None:
                pod.cpu_usage = min(cpu_usage, pod.cpu_limit * 1.2)
            if memory_usage is not None:
                pod.memory_usage = min(memory_usage, pod.memory_limit * 1.2)

        return {"status": "injected", "deployment": deployment_name}

    def inject_pod_crash(self, deployment_name: str, pod_index: int):
        """注入 Pod 崩溃"""
        deployment = self.deployments.get(deployment_name)
        if not deployment or pod_index >= len(deployment.pods):
            return {"error": "not found"}

        pod = deployment.pods[pod_index]
        pod.phase = PodPhase.FAILED
        pod.conditions["Ready"] = False
        deployment.replicas_ready -= 1

        event = {
            "type": "Warning",
            "reason": "BackOff",
            "message": f"Pod {pod.name} crashed",
            "timestamp": datetime.now().isoformat(),
        }
        self.events.append(event)
        return {"status": "crashed", "pod": pod.name}

    def scale_deployment(self, deployment_name: str, replicas: int):
        """模拟扩缩容"""
        deployment = self.deployments.get(deployment_name)
        if not deployment:
            return {"error": "not found"}

        old_replicas = deployment.replicas_desired
        deployment.replicas_desired = replicas
        # 调整 Pod 列表
        if replicas > len(deployment.pods):
            for i in range(len(deployment.pods), replicas):
                pod_name = f"{deployment_name}-{hash(deployment_name) % 10000}-{i}"
                pod = PodStatus(
                    name=pod_name, phase=PodPhase.RUNNING,
                    conditions={"Ready": True, "Initialized": True, "Scheduled": True},
                    containers=[{"name": deployment_name, "ready": True}],
                    cpu_usage=0.5, memory_usage=1024.0, cpu_limit=2.0, memory_limit=4096.0,
                    node_name=f"node-{i % 3}", ip=f"10.244.{i % 3}.{i + 1}",
                )
                deployment.pods.append(pod)
                self.pods[pod_name] = pod
        else:
            for pod in deployment.pods[replicas:]:
                pod.phase = PodPhase.SUCCEEDED
                pod.conditions["Ready"] = False
            deployment.pods = deployment.pods[:replicas]

        deployment.replicas_available = replicas
        deployment.replicas_ready = replicas
        return {"status": "scaled", "old": old_replicas, "new": replicas}
```

### 3.4 K8s 查询 API

```
GET  /api/v1/mock/k8s/deployments                    # 列出所有 Deployment
GET  /api/v1/mock/k8s/deployments/{name}             # 查询 Deployment 详情
GET  /api/v1/mock/k8s/deployments/{name}/pods        # 查询 Pod 列表
GET  /api/v1/mock/k8s/services                       # 列出所有 Service
GET  /api/v1/mock/k8s/events                         # 查询事件列表
POST /api/v1/mock/k8s/inject/pod-restart             # 注入 Pod 重启
POST /api/v1/mock/k8s/inject/resource-pressure       # 注入资源压力
POST /api/v1/mock/k8s/inject/pod-crash               # 注入 Pod 崩溃
POST /api/v1/mock/k8s/scale                          # 扩缩容
```

## 4. 中间件模拟器

### 4.1 DB 模拟器（MySQL）

```python
class MySQLSimulator:
    """MySQL 数据库模拟器"""

    def __init__(self, instance_name: str = "mysql-prod-01"):
        self.instance = instance_name
        self.slaves = [
            {"instance": f"{instance_name}-slave-01", "delay_seconds": 0.5, "status": "normal"},
            {"instance": f"{instance_name}-slave-02", "delay_seconds": 0.3, "status": "normal"},
        ]
        self.connection_pool = {"max": 200, "active": 80, "idle": 120, "waiting": 0}
        self.slow_queries = []
        self.lock_waits = []
        self.metrics_history = self._init_metrics_history()
        self.io_wait = 5.0  # %

    def inject_slave_delay(self, delay_seconds: float, slave_index: int = 0):
        """注入主从延迟"""
        if slave_index < len(self.slaves):
            self.slaves[slave_index]["delay_seconds"] = delay_seconds
            self.slaves[slave_index]["status"] = "lagging" if delay_seconds > 5 else "normal"

    def inject_connection_pool_exhaustion(self, active: int = 180):
        """注入连接池耗尽"""
        self.connection_pool["active"] = active
        self.connection_pool["idle"] = self.connection_pool["max"] - active
        self.connection_pool["waiting"] = max(0, active - self.connection_pool["max"] + 20)

    def inject_slow_query(self, sql: str, duration_ms: int, count: int = 1):
        """注入慢查询"""
        for _ in range(count):
            self.slow_queries.append({
                "sql": sql,
                "duration_ms": duration_ms,
                "timestamp": datetime.now().isoformat(),
                "rows_examined": 1500000,
                "rows_returned": 100,
                "index_used": "idx_created_at",
            })

    def inject_lock_wait(self, count: int = 5):
        """注入锁等待"""
        for _ in range(count):
            self.lock_waits.append({
                "transaction_id": f"tx-{random.randint(10000, 99999)}",
                "lock_type": "RECORD",
                "table": "orders",
                "waiting_seconds": random.uniform(5, 30),
                "timestamp": datetime.now().isoformat(),
            })

    def get_metrics(self, metric_names: List[str], start_time: str, end_time: str) -> dict:
        """获取指标数据"""
        result = {}
        for metric in metric_names:
            if metric == "active_connections":
                result[metric] = {
                    "current": self.connection_pool["active"],
                    "max": self.connection_pool["max"],
                    "usage_ratio": self.connection_pool["active"] / self.connection_pool["max"],
                }
            elif metric == "slow_query_count":
                result[metric] = {
                    "current": len(self.slow_queries),
                    "baseline_avg": 5,
                    "anomaly_ratio": max(1.0, len(self.slow_queries) / 5),
                }
            elif metric == "slave_delay_seconds":
                result[metric] = {
                    "current": self.slaves[0]["delay_seconds"],
                    "threshold": 5.0,
                    "exceeded": self.slaves[0]["delay_seconds"] > 5.0,
                }
            elif metric == "innodb_lock_waits":
                result[metric] = {"current": len(self.lock_waits)}
            elif metric == "io_wait_ratio":
                result[metric] = {"current": self.io_wait}
        return result
```

### 4.2 Redis 模拟器

```python
class RedisSimulator:
    """Redis 缓存模拟器"""

    def __init__(self, instance_name: str = "redis-cluster-01"):
        self.instance = instance_name
        self.used_memory = 4.0  # GB
        self.maxmemory = 8.0    # GB
        self.hit_rate = 0.95
        self.connected_clients = 50
        self.maxclients = 200
        self.ops_per_sec = 5000
        self.evicted_keys = 0
        self.hotkeys = []
        self.bigkeys = []
        self.slowlog = []

    def inject_memory_pressure(self, used_memory: float = 7.5):
        """注入内存压力"""
        self.used_memory = used_memory
        if used_memory / self.maxmemory > 0.85:
            self.evicted_keys = random.randint(100, 1000)

    def inject_hit_rate_drop(self, hit_rate: float = 0.70):
        """注入命中率下降"""
        self.hit_rate = hit_rate

    def inject_hotkey(self, key: str, qps: int = 15000):
        """注入热点 Key"""
        self.hotkeys.append({"key": key, "qps": qps, "type": "string", "size": "1KB"})

    def inject_bigkey(self, key: str, size_mb: float = 15):
        """注入大 Key"""
        self.bigkeys.append({"key": key, "size_mb": size_mb, "type": "hash"})

    def get_metrics(self, metric_names: List[str]) -> dict:
        """获取指标"""
        result = {}
        for metric in metric_names:
            mapping = {
                "used_memory": lambda: {"current_gb": self.used_memory, "max_gb": self.maxmemory,
                                        "usage_ratio": self.used_memory / self.maxmemory},
                "hit_rate": lambda: {"current": self.hit_rate, "baseline": 0.95,
                                     "drop_pp": (0.95 - self.hit_rate) * 100},
                "connected_clients": lambda: {"current": self.connected_clients, "max": self.maxclients,
                                              "usage_ratio": self.connected_clients / self.maxclients},
                "ops_per_sec": lambda: {"current": self.ops_per_sec},
                "evicted_keys": lambda: {"current": self.evicted_keys},
            }
            if metric in mapping:
                result[metric] = mapping[metric]()
        return result
```

### 4.3 Kafka/Mafka 模拟器

```python
class KafkaSimulator:
    """Kafka 消息队列模拟器"""

    def __init__(self, cluster: str = "kafka-prod-01"):
        self.cluster = cluster
        self.topics = {
            "order-events": {
                "partitions": 3,
                "consumer_groups": {
                    "order-consumer-group": {
                        "consumers": ["consumer-1", "consumer-2", "consumer-3"],
                        "lag_per_partition": [100, 100, 100],
                        "rebalance_count": 0,
                    }
                }
            }
        }
        self.produce_rate = 1000  # msg/s
        self.consume_rate = 1000  # msg/s

    def inject_consumer_offline(self, topic: str, group: str, consumer: str):
        """注入消费者离线"""
        topic_data = self.topics.get(topic, {})
        group_data = topic_data.get("consumer_groups", {}).get(group, {})
        if consumer in group_data.get("consumers", []):
            group_data["consumers"].remove(consumer)
            # 离线消费者的分区积压增长
            offline_partitions = len(group_data["lag_per_partition"]) - len(group_data["consumers"])
            for i in range(offline_partitions):
                group_data["lag_per_partition"][i] += 10000

    def inject_rebalance_storm(self, topic: str, group: str, count: int = 5):
        """注入 Rebalance 风暴"""
        group_data = self.topics[topic]["consumer_groups"][group]
        group_data["rebalance_count"] = count

    def inject_consume_rate_drop(self, rate: int = 300):
        """注入消费速率下降"""
        self.consume_rate = rate

    def get_consumer_lag(self, topic: str, group: str) -> dict:
        """获取消费者积压详情"""
        topic_data = self.topics.get(topic, {})
        group_data = topic_data.get("consumer_groups", {}).get(group, {})
        return {
            "cluster": self.cluster,
            "topic": topic,
            "consumer_group": group,
            "total_lag": sum(group_data.get("lag_per_partition", [])),
            "partitions": [
                {"partition": i, "lag": lag,
                 "consumer": group_data["consumers"][i] if i < len(group_data["consumers"]) else None}
                for i, lag in enumerate(group_data.get("lag_per_partition", []))
            ],
            "rebalance_events": group_data.get("rebalance_count", 0),
            "produce_rate": self.produce_rate,
            "consume_rate": self.consume_rate,
        }
```

### 4.4 中间件查询 API

```
# DB
GET  /api/v1/mock/db/{instance}/metrics
GET  /api/v1/mock/db/{instance}/slow-log
GET  /api/v1/mock/db/{instance}/topology
POST /api/v1/mock/db/{instance}/inject/slave-delay
POST /api/v1/mock/db/{instance}/inject/connection-pool
POST /api/v1/mock/db/{instance}/inject/slow-query
POST /api/v1/mock/db/{instance}/inject/lock-wait

# Redis
GET  /api/v1/mock/redis/{instance}/metrics
GET  /api/v1/mock/redis/{instance}/hotkeys
GET  /api/v1/mock/redis/{instance}/topology
POST /api/v1/mock/redis/{instance}/inject/memory-pressure
POST /api/v1/mock/redis/{instance}/inject/hit-rate-drop
POST /api/v1/mock/redis/{instance}/inject/hotkey

# Kafka
GET  /api/v1/mock/kafka/{cluster}/topics/{topic}/lag
GET  /api/v1/mock/kafka/{cluster}/metrics
POST /api/v1/mock/kafka/{cluster}/inject/consumer-offline
POST /api/v1/mock/kafka/{cluster}/inject/rebalance
POST /api/v1/mock/kafka/{cluster}/inject/consume-rate-drop
```

## 5. 微服务模拟器

### 5.1 服务拓扑与调用链模拟

```python
class MicroserviceSimulator:
    """微服务调用链模拟器"""

    def __init__(self):
        self.services = self._init_service_topology()
        self.traces = []
        self.metrics = self._init_metrics()

    def _init_service_topology(self) -> dict:
        """初始化服务拓扑"""
        return {
            "order-service": {
                "upstream": [
                    {"service": "api-gateway", "call_type": "HTTP", "qps": 1200, "error_rate": 0.01},
                ],
                "downstream": [
                    {"service": "mysql-prod-01", "call_type": "JDBC", "qps": 1500, "error_rate": 0.01, "avg_rt_ms": 50},
                    {"service": "redis-cluster-01", "call_type": "Redis", "qps": 3000, "error_rate": 0.0, "avg_rt_ms": 2},
                    {"service": "kafka-prod-01", "call_type": "Kafka", "qps": 800, "error_rate": 0.0, "avg_rt_ms": 5},
                    {"service": "payment-service", "call_type": "RPC", "qps": 600, "error_rate": 0.01, "avg_rt_ms": 80},
                    {"service": "inventory-service", "call_type": "RPC", "qps": 400, "error_rate": 0.0, "avg_rt_ms": 30},
                ],
            },
            "payment-service": {
                "downstream": [
                    {"service": "mysql-prod-02", "call_type": "JDBC", "qps": 500, "error_rate": 0.01, "avg_rt_ms": 40},
                    {"service": "redis-cluster-01", "call_type": "Redis", "qps": 1000, "error_rate": 0.0, "avg_rt_ms": 1},
                ],
            },
        }

    def _init_metrics(self) -> dict:
        """初始化指标基线"""
        return {
            "order-service": {
                "qps": {"baseline": 1200, "current": 1200, "history": []},
                "tp99": {"baseline": 100, "current": 100, "history": []},
                "error_rate": {"baseline": 0.01, "current": 0.01, "history": []},
                "cpu_usage": {"baseline": 0.3, "current": 0.3, "history": []},
                "memory_usage": {"baseline": 0.4, "current": 0.4, "history": []},
            },
        }

    def inject_service_timeout(self, service_name: str, downstream: str, tp99_ms: int = 800):
        """注入服务调用超时"""
        for svc in self.services.get(service_name, {}).get("downstream", []):
            if svc["service"] == downstream:
                svc["avg_rt_ms"] = tp99_ms
                svc["error_rate"] = 0.05
                break
        # 更新指标
        self.metrics[service_name]["tp99"]["current"] = tp99_ms
        self.metrics[service_name]["error_rate"]["current"] = 0.05

    def inject_traffic_spike(self, service_name: str, qps: int = 5000):
        """注入流量突增"""
        self.metrics[service_name]["qps"]["current"] = qps

    def inject_error_rate_spike(self, service_name: str, error_rate: float = 0.15):
        """注入错误率突增"""
        self.metrics[service_name]["error_rate"]["current"] = error_rate

    def generate_trace(self, service_name: str, status: str = "success") -> dict:
        """生成调用链"""
        trace_id = f"trace-{uuid.uuid4().hex[:12]}"
        spans = [{
            "service": service_name,
            "operation": "handleRequest",
            "duration_ms": self.metrics[service_name]["tp99"]["current"],
            "status": status,
            "timestamp": datetime.now().isoformat(),
        }]
        # 添加下游 span
        for dep in self.services.get(service_name, {}).get("downstream", []):
            span_status = "error" if dep["error_rate"] > 0.05 else "success"
            spans.append({
                "service": dep["service"],
                "operation": f"call_{dep['service']}",
                "duration_ms": dep["avg_rt_ms"],
                "status": span_status,
                "call_type": dep["call_type"],
            })
        return {"trace_id": trace_id, "status": status, "spans": spans}

    def get_metrics(self, service_name: str, metric_name: str, start_time: str, end_time: str) -> dict:
        """获取服务指标"""
        svc_metrics = self.metrics.get(service_name, {})
        metric = svc_metrics.get(metric_name, {})
        # 生成时序数据点
        data_points = self._generate_time_series(metric, start_time, end_time)
        return {
            "metric": metric_name,
            "service": service_name,
            "data_points": data_points,
            "aggregation": {
                "min": min(p["value"] for p in data_points),
                "max": max(p["value"] for p in data_points),
                "avg": sum(p["value"] for p in data_points) / len(data_points),
                "current": metric.get("current", 0),
                "baseline": metric.get("baseline", 0),
            }
        }

    def _generate_time_series(self, metric: dict, start_time: str, end_time: str) -> List[dict]:
        """生成时序数据点"""
        points = []
        start = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        current = datetime.now()
        baseline = metric.get("baseline", 0)
        current_val = metric.get("current", 0)

        # 生成最近 30 分钟的数据
        for i in range(30):
            t = current - timedelta(minutes=30 - i)
            if i < 25:
                val = baseline + random.uniform(-baseline * 0.1, baseline * 0.1)
            else:
                # 最后 5 分钟注入异常
                val = current_val + random.uniform(-current_val * 0.05, current_val * 0.05)
            points.append({"timestamp": t.isoformat(), "value": round(val, 2)})
        return points
```

### 5.2 微服务查询 API

```
GET  /api/v1/mock/service/{name}/topology              # 服务拓扑
GET  /api/v1/mock/service/{name}/metrics/{metric}      # 服务指标
GET  /api/v1/mock/service/{name}/traces                # 调用链
GET  /api/v1/mock/service/{name}/logs                  # 服务日志
POST /api/v1/mock/service/{name}/inject/timeout        # 注入超时
POST /api/v1/mock/service/{name}/inject/traffic-spike  # 注入流量突增
POST /api/v1/mock/service/{name}/inject/error-rate     # 注入错误率
```

## 6. 告警模拟器

```python
class AlertSimulator:
    """告警事件模拟器"""

    SCENARIOS = {
        "db_slave_delay_timeout": {
            "alert": {
                "alert_id": "alert-scenario-001",
                "service_name": "order-service",
                "alert_type": "timeout",
                "severity": "P1",
                "timestamp": "",  # 运行时填充
                "description": "order-service TP99 延迟突增至 800ms",
                "labels": {"cluster": "prod-cluster-01", "env": "production"},
            },
            "injections": [
                {"target": "db", "action": "inject_slave_delay", "params": {"delay_seconds": 15.0}},
                {"target": "db", "action": "inject_slow_query", "params": {"sql": "SELECT * FROM orders", "duration_ms": 800, "count": 30}},
                {"target": "service", "action": "inject_timeout", "params": {"service_name": "order-service", "downstream": "mysql-prod-01", "tp99_ms": 800}},
            ],
            "expected_root_cause": "数据库主从延迟导致 order-service 查询超时",
            "expected_confidence_min": 0.85,
        },
        "oom_restart": {
            "alert": {
                "alert_id": "alert-scenario-002",
                "service_name": "order-service",
                "alert_type": "error_rate",
                "severity": "P0",
                "timestamp": "",
                "description": "order-service 错误率突增至 15%",
                "labels": {"cluster": "prod-cluster-01", "env": "production"},
            },
            "injections": [
                {"target": "k8s", "action": "inject_pod_restart", "params": {"deployment_name": "order-service", "pod_index": 0, "reason": "OOMKilled"}},
                {"target": "service", "action": "inject_error_rate", "params": {"service_name": "order-service", "error_rate": 0.15}},
            ],
            "expected_root_cause": "服务内存溢出导致 Pod 被 Kill 并重启",
            "expected_confidence_min": 0.9,
        },
        "kafka_consumer_lag": {
            "alert": {
                "alert_id": "alert-scenario-003",
                "service_name": "order-service",
                "alert_type": "resource",
                "severity": "P1",
                "timestamp": "",
                "description": "order-service Kafka 消费积压 50000+",
                "labels": {"cluster": "prod-cluster-01", "env": "production"},
            },
            "injections": [
                {"target": "kafka", "action": "inject_consumer_offline", "params": {"topic": "order-events", "group": "order-consumer-group", "consumer": "consumer-3"}},
                {"target": "kafka", "action": "inject_consume_rate_drop", "params": {"rate": 300}},
            ],
            "expected_root_cause": "Kafka 消费者离线导致消息积压",
            "expected_confidence_min": 0.85,
        },
        "change_induced_failure": {
            "alert": {
                "alert_id": "alert-scenario-004",
                "service_name": "order-service",
                "alert_type": "timeout",
                "severity": "P1",
                "timestamp": "",
                "description": "order-service TP99 延迟突增至 600ms",
                "labels": {"cluster": "prod-cluster-01", "env": "production"},
            },
            "injections": [
                {"target": "db", "action": "inject_connection_pool_exhaustion", "params": {"active": 180}},
            ],
            "changes": [
                {"change_id": "chg-mock-001", "type": "config", "description": "调整 DB 连接池 innodb_buffer_pool_size 从 4G 到 8G",
                 "operator": "testuser", "timestamp": "", "risk_level": "medium", "related_service": "mysql-prod-01"},
            ],
            "expected_root_cause": "配置变更导致连接池参数异常",
            "expected_confidence_min": 0.8,
        },
        "redis_memory_pressure": {
            "alert": {
                "alert_id": "alert-scenario-005",
                "service_name": "order-service",
                "alert_type": "error_rate",
                "severity": "P2",
                "timestamp": "",
                "description": "order-service 缓存命中率下降，错误率上升",
                "labels": {"cluster": "prod-cluster-01", "env": "production"},
            },
            "injections": [
                {"target": "redis", "action": "inject_memory_pressure", "params": {"used_memory": 7.5}},
                {"target": "redis", "action": "inject_hit_rate_drop", "params": {"hit_rate": 0.70}},
                {"target": "redis", "action": "inject_bigkey", "params": {"key": "order:cache:batch", "size_mb": 15}},
            ],
            "expected_root_cause": "Redis 内存压力导致缓存命中率下降",
            "expected_confidence_min": 0.8,
        },
    }

    def get_scenario(self, scenario_name: str) -> dict:
        """获取测试场景"""
        scenario = self.SCENARIOS.get(scenario_name)
        if not scenario:
            return None
        # 填充当前时间
        now = datetime.now().isoformat()
        scenario = json.loads(json.dumps(scenario))  # deep copy
        scenario["alert"]["timestamp"] = now
        for inj in scenario.get("injections", []):
            if "timestamp" in inj.get("params", {}):
                inj["params"]["timestamp"] = now
        for change in scenario.get("changes", []):
            change["timestamp"] = now
        return scenario

    def list_scenarios(self) -> List[dict]:
        """列出所有测试场景"""
        return [
            {"name": name, "description": s["alert"]["description"], "severity": s["alert"]["severity"]}
            for name, s in self.SCENARIOS.items()
        ]
```

### 6.1 场景管理 API

```
GET  /api/v1/mock/scenarios                           # 列出所有测试场景
GET  /api/v1/mock/scenarios/{name}                    # 获取场景详情
POST /api/v1/mock/scenarios/{name}/apply              # 应用场景（执行故障注入）
POST /api/v1/mock/scenarios/{name}/run                # 端到端执行：注入+分析+验证
POST /api/v1/mock/reset                               # 重置所有模拟器状态
```

## 7. 端到端验证流程

```
POST /api/v1/mock/scenarios/{name}/run

1. 重置模拟环境
2. 应用故障注入（按场景配置）
3. 生成告警事件，提交到 DeepRCA-Agent
4. 等待分析完成（轮询 /api/v1/analyze/{trace_id}/status）
5. 获取分析结果
6. 对比预期根因：
   - root_cause.conclusion 与 expected_root_cause 语义匹配
   - root_cause.confidence >= expected_confidence_min
7. 返回验证报告

Response:
{
  "scenario": "db_slave_delay_timeout",
  "status": "passed",
  "trace_id": "trace-xxx",
  "analysis_duration_seconds": 35,
  "actual_root_cause": "数据库主从延迟导致 order-service 查询超时",
  "expected_root_cause": "数据库主从延迟导致 order-service 查询超时",
  "root_cause_matched": true,
  "actual_confidence": 0.9,
  "expected_confidence_min": 0.85,
  "confidence_passed": true,
  "evidence_count": 12,
  "suggestions_count": 3,
  "details": { ... }
}
```

## 8. 验证 API 完整列表

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/mock/health | 模拟环境健康检查 |
| POST | /api/v1/mock/reset | 重置所有模拟器 |
| GET | /api/v1/mock/scenarios | 列出测试场景 |
| GET | /api/v1/mock/scenarios/{name} | 场景详情 |
| POST | /api/v1/mock/scenarios/{name}/apply | 应用故障注入 |
| POST | /api/v1/mock/scenarios/{name}/run | 端到端验证 |
| GET | /api/v1/mock/k8s/deployments | K8s Deployment 列表 |
| GET | /api/v1/mock/k8s/deployments/{name} | Deployment 详情 |
| GET | /api/v1/mock/k8s/deployments/{name}/pods | Pod 列表 |
| GET | /api/v1/mock/k8s/events | K8s 事件 |
| POST | /api/v1/mock/k8s/inject/pod-restart | 注入 Pod 重启 |
| POST | /api/v1/mock/k8s/inject/resource-pressure | 注入资源压力 |
| POST | /api/v1/mock/k8s/inject/pod-crash | 注入 Pod 崩溃 |
| POST | /api/v1/mock/k8s/scale | 扩缩容 |
| GET | /api/v1/mock/db/{instance}/metrics | DB 指标 |
| GET | /api/v1/mock/db/{instance}/slow-log | DB 慢日志 |
| GET | /api/v1/mock/db/{instance}/topology | DB 拓扑 |
| POST | /api/v1/mock/db/{instance}/inject/slave-delay | 注入主从延迟 |
| POST | /api/v1/mock/db/{instance}/inject/connection-pool | 注入连接池耗尽 |
| POST | /api/v1/mock/db/{instance}/inject/slow-query | 注入慢查询 |
| POST | /api/v1/mock/db/{instance}/inject/lock-wait | 注入锁等待 |
| GET | /api/v1/mock/redis/{instance}/metrics | Redis 指标 |
| GET | /api/v1/mock/redis/{instance}/hotkeys | Redis 热点 Key |
| GET | /api/v1/mock/redis/{instance}/topology | Redis 拓扑 |
| POST | /api/v1/mock/redis/{instance}/inject/memory-pressure | 注入内存压力 |
| POST | /api/v1/mock/redis/{instance}/inject/hit-rate-drop | 注入命中率下降 |
| POST | /api/v1/mock/redis/{instance}/inject/hotkey | 注入热点 Key |
| GET | /api/v1/mock/kafka/{cluster}/topics/{topic}/lag | Kafka 消费积压 |
| GET | /api/v1/mock/kafka/{cluster}/metrics | Kafka 指标 |
| POST | /api/v1/mock/kafka/{cluster}/inject/consumer-offline | 注入消费者离线 |
| POST | /api/v1/mock/kafka/{cluster}/inject/rebalance | 注入 Rebalance |
| POST | /api/v1/mock/kafka/{cluster}/inject/consume-rate-drop | 注入消费速率下降 |
| GET | /api/v1/mock/service/{name}/topology | 服务拓扑 |
| GET | /api/v1/mock/service/{name}/metrics/{metric} | 服务指标 |
| GET | /api/v1/mock/service/{name}/traces | 调用链 |
| GET | /api/v1/mock/service/{name}/logs | 服务日志 |
| POST | /api/v1/mock/service/{name}/inject/timeout | 注入超时 |
| POST | /api/v1/mock/service/{name}/inject/traffic-spike | 注入流量突增 |
| POST | /api/v1/mock/service/{name}/inject/error-rate | 注入错误率 |

## 9. 技术实现

### 9.1 Docker Compose 一键启动

```yaml
# docker-compose.yml
version: '3.8'

services:
  deeprca-agent:
    build: .
    ports:
      - "8000:8000"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - LLM_MODEL=gpt-4o
      - REDIS_URL=redis://redis:6379
    depends_on:
      - redis

  mock-env:
    build:
      context: .
      dockerfile: mock_env/Dockerfile
    ports:
      - "8001:8001"
    environment:
      - LOG_LEVEL=INFO

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

### 9.2 FastAPI 服务入口

```python
# mock_env/server.py
from fastapi import FastAPI
from mock_env.k8s_simulator import K8sSimulator
from mock_env.middleware_simulator import MySQLSimulator, RedisSimulator, KafkaSimulator
from mock_env.microservice_simulator import MicroserviceSimulator
from mock_env.alert_simulator import AlertSimulator

app = FastAPI(title="DeepRCA Mock Environment", version="1.0")

# 初始化模拟器
k8s_sim = K8sSimulator()
mysql_sim = MySQLSimulator()
redis_sim = RedisSimulator()
kafka_sim = KafkaSimulator()
service_sim = MicroserviceSimulator()
alert_sim = AlertSimulator()

# 注册路由...
```

## 10. 测试场景矩阵

| 场景 | 故障类型 | 注入组件 | 预期根因 | 最小置信度 |
|------|----------|----------|----------|------------|
| db_slave_delay_timeout | DB 主从延迟 | DB + Service | DB 延迟导致查询超时 | 0.85 |
| oom_restart | OOM 重启 | K8s + Service | 内存溢出导致 Pod 重启 | 0.90 |
| kafka_consumer_lag | 消费者离线 | Kafka | 消费者离线导致积压 | 0.85 |
| change_induced_failure | 配置变更 | DB + Change | 配置变更导致连接池异常 | 0.80 |
| redis_memory_pressure | Redis 内存压力 | Redis + Service | 内存压力导致命中率下降 | 0.80 |
| traffic_spike_saturation | 流量突增 | Service + K8s | 流量突增导致资源饱和 | 0.80 |
| rpc_circuit_breaker | 熔断触发 | Service | 下游异常触发熔断 | 0.85 |
| multi_dimension_anomaly | 多维度共振 | DB + Redis + K8s | 多维度异常共振 | 0.75 |
