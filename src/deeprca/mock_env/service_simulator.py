"""微服务调用链模拟器 — 生成监控指标、日志、链路、拓扑、变更、告警模拟数据。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：6 类数据生成器</td><td>REQ: 20260713-总体架构, TECH: 04b §3.5</td></tr>
<tr><td>0.2.0</td><td>对齐 PRD-05：增强服务拓扑与调用链模拟</td><td>PRD-05 §5</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _iso_range(start: str, end: str, points: int = 60) -> list[str]:
    """生成时间戳序列。"""
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        s = datetime.now(timezone.utc) - timedelta(minutes=30)
        e = datetime.now(timezone.utc)
    if e <= s:
        e = s + timedelta(minutes=30)
    delta = (e - s) / max(points - 1, 1)
    return [(s + delta * i).strftime("%Y-%m-%dT%H:%M:%S+00:00") for i in range(points)]


class MicroserviceSimulator:
    """微服务调用链模拟器。

    管理各服务的指标、日志、链路、拓扑、变更、告警数据。
    支持场景注入（通过 scenarios 模块触发）。
    """

    SERVICES = [
        "order-service",
        "payment-service",
        "user-service",
        "inventory-service",
        "gateway-service",
        "db-proxy",
        "redis-cluster",
        "kafka-broker",
    ]

    # 服务拓扑关系
    TOPOLOGY: dict[str, dict[str, list[str]]] = {
        "order-service": {
            "upstream": ["gateway-service"],
            "downstream": ["payment-service", "inventory-service", "user-service",
                           "mysql-prod-01", "redis-cluster-01", "kafka-prod-01"],
        },
        "payment-service": {
            "upstream": ["order-service"],
            "downstream": ["mysql-prod-02", "redis-cluster-01"],
        },
        "inventory-service": {
            "upstream": ["order-service"],
            "downstream": ["mysql-prod-01"],
        },
        "user-service": {
            "upstream": ["order-service", "gateway-service"],
            "downstream": ["mysql-prod-01", "redis-cluster-01"],
        },
        "gateway-service": {
            "upstream": [],
            "downstream": ["order-service", "user-service"],
        },
    }

    # 指标基线值范围
    METRIC_BASELINES: dict[str, tuple[float, float]] = {
        "qps": (800, 1200),
        "error_rate": (0.1, 0.5),
        "tp99": (50, 120),
        "cpu_usage": (15, 45),
        "memory_usage": (30, 60),
    }

    def __init__(self) -> None:
        self._topology: dict[str, dict] = {}
        self._changes: list[dict] = []
        self._alerts: list[dict] = []
        self._active_scenario: str | None = None
        self._scenario_data: dict[str, Any] = {}
        self._metrics_override: dict[str, dict[str, float]] = {}
        self._init_topology()

    def _init_topology(self) -> None:
        """初始化服务拓扑关系。"""
        self._topology = {
            svc: {"upstream": list(data["upstream"]), "downstream": list(data["downstream"])}
            for svc, data in self.TOPOLOGY.items()
        }

    # ─────────────────────────────────────
    # 指标
    # ─────────────────────────────────────
    def get_metrics(
        self,
        service_name: str,
        metric_name: str,
        start_time: str,
        end_time: str,
        labels: dict | None = None,
    ) -> dict:
        """生成时序指标数据。"""
        timestamps = _iso_range(start_time, end_time, 60)
        scenario = self._active_scenario
        lo, hi = self.METRIC_BASELINES.get(metric_name, (0, 100))

        # 检查是否有 override（故障注入设置的值）
        override_val = self._metrics_override.get(service_name, {}).get(metric_name)

        data_points: list[dict] = []
        mid_idx = len(timestamps) // 2

        for i, ts in enumerate(timestamps):
            if override_val is not None and i >= mid_idx:
                # 注入后使用 override 值（带少量噪声）
                val = round(override_val * (1 + random.uniform(-0.05, 0.05)), 2)
            elif scenario and i >= mid_idx:
                val = self._scenario_metric_value(scenario, metric_name, lo, hi)
            else:
                val = round(random.uniform(lo, hi), 2)
            data_points.append({"timestamp": ts, "value": val})

        values = [p["value"] for p in data_points]
        return {
            "service": service_name,
            "metric": metric_name,
            "data_points": data_points,
            "aggregation": {
                "min": min(values) if values else 0,
                "max": max(values) if values else 0,
                "avg": round(sum(values) / len(values), 2) if values else 0,
                "current": values[-1] if values else 0,
                "baseline": round((lo + hi) / 2, 2),
            },
        }

    def _scenario_metric_value(self, scenario: str, metric_name: str, lo: float, hi: float) -> float:
        """根据场景生成异常指标值。

        支持 alert_simulator 与 service_simulator 之间的场景名映射，
        确保端到端场景能够触发预期的指标异常。
        """
        # 场景名映射：alert_simulator 的预设名 -> service 侧识别的场景名
        scenario_map = {
            "oom_restart": "pod_crash",
            "change_induced_failure": "change_induced",
            "redis_memory_pressure": "redis_timeout",
            "traffic_spike_saturation": "traffic_spike",
            "rpc_circuit_breaker": "db_slow_query",  # 借延迟场景表达 RPC 超时
            "multi_dimension_anomaly": "pod_crash",  # 多维度异常以 pod_crash 为主
        }
        mapped = scenario_map.get(scenario, scenario)

        if mapped == "pod_crash" and metric_name == "error_rate":
            return round(random.uniform(10, 20), 2)
        elif mapped == "pod_crash" and metric_name == "cpu_usage":
            return round(random.uniform(85, 98), 1)
        elif mapped == "db_slow_query" and metric_name == "tp99":
            return round(random.uniform(800, 2000), 2)
        elif mapped == "redis_timeout" and metric_name == "tp99":
            return round(random.uniform(500, 1500), 2)
        elif mapped == "traffic_spike" and metric_name == "qps":
            return round(random.uniform(3000, 5000), 2)
        elif mapped == "kafka_lag" and metric_name == "error_rate":
            return round(random.uniform(5, 15), 2)
        return round(random.uniform(lo, hi), 2)

    # ─────────────────────────────────────
    # 日志
    # ─────────────────────────────────────
    def get_logs(
        self,
        service_name: str,
        start_time: str,
        end_time: str,
        level: str = "ERROR",
        keyword: str = "",
        limit: int = 100,
    ) -> dict:
        """生成错误日志数据。"""
        scenario = self._active_scenario
        log_templates: dict[str, list[str]] = {
            "pod_crash": [
                "Connection refused to {svc}",
                "Pod {pod} CrashLoopBackOff, restarting...",
                "Health check failed for {svc}",
                "java.net.ConnectException: Connection refused",
            ],
            "oom_restart": [
                "java.lang.OutOfMemoryError: Java heap space",
                "Pod {pod} OOMKilled (exit code 137), restarting...",
                "CrashLoopBackOff: backoff restarting failed container",
            ],
            "db_slow_query": [
                "SQL execution timeout after 3000ms: SELECT * FROM orders WHERE...",
                "HikariPool-1 - Connection is not available, request timed out",
                "Deadlock found when trying to get lock; try restarting transaction",
            ],
            "redis_timeout": [
                "Redis command timeout: GET user:session:*",
                "JedisConnectionException: Could not get a resource from the pool",
                "Redis pipeline execution failed: timeout",
            ],
            "redis_memory_pressure": [
                "Redis command timeout: GET user:session:*",
                "JedisConnectionException: Could not get a resource from the pool",
                "Redis pipeline execution failed: timeout",
            ],
            "kafka_lag": [
                "Kafka consumer lag detected: 50000 messages behind",
                "Consumer heartbeat timeout, triggering rebalance",
                "Partition assignment failed for consumer-3",
            ],
            "deployment_failure": [
                "NullPointerException at com.order.service.OrderService.process()",
                "Failed to bind properties under 'spring.datasource.url'",
                "Bean creation exception: OrderController",
            ],
            "change_induced": [
                "HikariPool-1 - Connection is not available, request timed out",
                "Connection pool exhausted: active=180, max=200",
            ],
            "change_induced_failure": [
                "HikariPool-1 - Connection is not available, request timed out",
                "Connection pool exhausted: active=180, max=200",
            ],
            "traffic_spike": [
                "Request queue full, rejecting connections",
                "Rate limit exceeded for {svc}",
                "Thread pool exhausted, max=200, active=200",
            ],
            "traffic_spike_saturation": [
                "Request queue full, rejecting connections",
                "Rate limit exceeded for {svc}",
                "Thread pool exhausted, max=200, active=200",
            ],
            "rpc_circuit_breaker": [
                "Circuit breaker opened for payment-service",
                "Connection refused to payment-service",
                "Request timeout after 1000ms: payment-service",
            ],
            "multi_dimension_anomaly": [
                "SQL execution timeout after 3000ms: SELECT * FROM orders JOIN...",
                "Redis command timeout: GET user:session:*",
                "Pod {pod} CrashLoopBackOff, restarting...",
            ],
        }

        # 场景名映射后选择模板
        scenario_map = {
            "oom_restart": "oom_restart",
            "change_induced_failure": "change_induced_failure",
            "redis_memory_pressure": "redis_memory_pressure",
            "traffic_spike_saturation": "traffic_spike_saturation",
            "rpc_circuit_breaker": "rpc_circuit_breaker",
            "multi_dimension_anomaly": "multi_dimension_anomaly",
        }
        mapped = scenario_map.get(scenario, scenario)

        templates = log_templates.get(mapped, [
            "Unexpected error in {svc}",
            "Request processing failed",
            "Service unavailable: {svc}",
        ])

        logs: list[dict] = []
        timestamps = _iso_range(start_time, end_time, min(limit, 50))
        for i, ts in enumerate(timestamps):
            tpl = random.choice(templates)
            msg = tpl.format(svc=service_name, pod=f"{service_name}-{random.randint(1000, 9999)}")
            if keyword and keyword.lower() not in msg.lower():
                continue
            logs.append({
                "timestamp": ts,
                "level": level,
                "service": service_name,
                "message": msg,
                "trace_id": f"trace-{random.randint(100000, 999999)}",
                "host": f"node-{random.randint(1, 5)}",
            })

        return {"service": service_name, "logs": logs[:limit]}

    # ─────────────────────────────────────
    # 链路追踪
    # ─────────────────────────────────────
    def get_traces(
        self,
        service_name: str,
        start_time: str,
        end_time: str,
        trace_id: str = "",
        limit: int = 50,
    ) -> dict:
        """生成调用链路数据。"""
        scenario = self._active_scenario
        downstream = self._topology.get(service_name, {}).get("downstream", [])

        traces: list[dict] = []
        timestamps = _iso_range(start_time, end_time, min(limit, 30))

        for ts in timestamps:
            tid = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
            spans: list[dict] = [{
                "span_id": f"span-{random.randint(1000, 9999)}",
                "service": service_name,
                "operation": f"{service_name}.handle_request",
                "duration_ms": round(random.uniform(5, 30), 2),
                "status": "OK",
                "timestamp": ts,
            }]

            for ds in downstream[:3]:
                duration = round(random.uniform(10, 50), 2)
                status = "OK"
                if scenario and random.random() > 0.6:
                    if scenario == "db_slow_query" and "mysql" in ds:
                        duration = round(random.uniform(500, 2000), 2)
                        status = "SLOW"
                    elif scenario == "redis_timeout" and "redis" in ds:
                        duration = round(random.uniform(300, 1000), 2)
                        status = "ERROR"
                    elif scenario == "pod_crash":
                        status = "ERROR"
                        duration = round(random.uniform(1, 5), 2)
                    elif scenario == "kafka_lag" and "kafka" in ds:
                        duration = round(random.uniform(100, 500), 2)
                        status = "SLOW"

                spans.append({
                    "span_id": f"span-{random.randint(1000, 9999)}",
                    "service": ds,
                    "operation": f"{ds}.process",
                    "duration_ms": duration,
                    "status": status,
                    "timestamp": ts,
                    "parent_span_id": spans[0]["span_id"],
                })

            traces.append({
                "trace_id": tid,
                "spans": spans,
                "total_duration_ms": round(sum(s["duration_ms"] for s in spans), 2),
                "timestamp": ts,
            })

        return {"service": service_name, "traces": traces}

    # ─────────────────────────────────────
    # 拓扑
    # ─────────────────────────────────────
    def get_topology(self, service_name: str, depth: int = 2) -> dict:
        """返回服务拓扑关系。"""
        upstream = self._collect_upstream(service_name, depth)
        downstream = self._collect_downstream(service_name, depth)
        return {
            "service": service_name,
            "upstream": upstream,
            "downstream": downstream,
        }

    def _collect_upstream(self, service: str, depth: int, visited: set | None = None) -> list[dict]:
        if depth <= 0 or service not in self._topology:
            return []
        visited = visited or set()
        if service in visited:
            return []
        visited.add(service)
        result: list[dict] = []
        for up in self._topology.get(service, {}).get("upstream", []):
            result.append({"service": up, "relation": "upstream", "depth": 1})
            result.extend(
                {"service": s["service"], "relation": "upstream", "depth": s["depth"] + 1}
                for s in self._collect_upstream(up, depth - 1, visited)
            )
        return result

    def _collect_downstream(self, service: str, depth: int, visited: set | None = None) -> list[dict]:
        if depth <= 0 or service not in self._topology:
            return []
        visited = visited or set()
        if service in visited:
            return []
        visited.add(service)
        result: list[dict] = []
        for ds in self._topology.get(service, {}).get("downstream", []):
            result.append({"service": ds, "relation": "downstream", "depth": 1})
            result.extend(
                {"service": s["service"], "relation": "downstream", "depth": s["depth"] + 1}
                for s in self._collect_downstream(ds, depth - 1, visited)
            )
        return result

    # ─────────────────────────────────────
    # 变更记录
    # ─────────────────────────────────────
    def get_changes(self, service_name: str, time_window: int = 3600) -> dict:
        """生成变更记录数据。

        优先使用 set_scenario 传入的显式 changes 数据，
        如果没有则根据场景名自行生成。
        """
        scenario = self._active_scenario
        now = datetime.now(timezone.utc)
        changes: list[dict] = []

        # 优先使用显式传入的 changes 数据（如 change_induced_failure 场景）
        explicit_changes = self._scenario_data.get("changes", [])
        if explicit_changes:
            for ch in explicit_changes:
                change = dict(ch)
                if not change.get("timestamp"):
                    change["timestamp"] = (now - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                if not change.get("service"):
                    change["service"] = service_name
                changes.append(change)
            return {"service": service_name, "changes": changes}

        # 支持 alert_simulator 与 service_simulator 之间的场景名映射
        scenario_map = {
            "change_induced_failure": "change_induced",
        }
        mapped = scenario_map.get(scenario, scenario)

        if mapped == "deployment_failure":
            changes.append({
                "change_id": f"chg-{random.randint(10000, 99999)}",
                "service": service_name,
                "type": "deployment",
                "operator": "ci-bot",
                "description": f"Deployed {service_name}:v1.2.4 (config change)",
                "timestamp": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "version": "v1.2.4",
                "previous_version": "v1.2.3",
                "risk_level": "high",
            })
        elif mapped == "change_induced":
            changes.append({
                "change_id": f"chg-{random.randint(10000, 99999)}",
                "service": service_name,
                "type": "config",
                "operator": "admin",
                "description": "调整 DB 连接池 innodb_buffer_pool_size 从 4G 到 8G",
                "timestamp": (now - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "risk_level": "medium",
                "related_service": "mysql-prod-01",
            })
        else:
            if random.random() > 0.5:
                changes.append({
                    "change_id": f"chg-{random.randint(10000, 99999)}",
                    "service": service_name,
                    "type": "config",
                    "operator": "admin",
                    "description": "Updated timeout configuration",
                    "timestamp": (now - timedelta(minutes=random.randint(30, 120))).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    "risk_level": "low",
                })

        return {"service": service_name, "changes": changes}

    # ─────────────────────────────────────
    # 关联告警
    # ─────────────────────────────────────
    def get_alerts(self, service_name: str, time_window: int = 1800) -> dict:
        """生成关联告警数据。"""
        scenario = self._active_scenario
        now = datetime.now(timezone.utc)
        alerts: list[dict] = []

        if scenario:
            downstream = self._topology.get(service_name, {}).get("downstream", [])
            for ds in downstream[:2]:
                alerts.append({
                    "alert_id": f"alt-{random.randint(10000, 99999)}",
                    "service": ds,
                    "alert_type": "custom",
                    "severity": random.choice(["P1", "P2"]),
                    "description": f"{ds} latency increased",
                    "timestamp": (now - timedelta(minutes=random.randint(1, 15))).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    "labels": {"cluster": "prod", "env": "production"},
                })

        return {"service": service_name, "alerts": alerts}

    # ─────────────────────────────────────
    # 故障注入
    # ─────────────────────────────────────
    def inject_service_timeout(self, service_name: str, downstream: str = "", tp99_ms: int = 800) -> dict[str, Any]:
        """注入服务调用超时。"""
        if not downstream:
            downstream_list = self._topology.get(service_name, {}).get("downstream", [])
            downstream = downstream_list[0] if downstream_list else ""
        self._metrics_override.setdefault(service_name, {})["tp99"] = float(tp99_ms)
        self._metrics_override.setdefault(service_name, {})["error_rate"] = 5.0
        return {"status": "injected", "service": service_name, "downstream": downstream, "tp99_ms": tp99_ms}

    def inject_traffic_spike(self, service_name: str, qps: int = 5000) -> dict[str, Any]:
        """注入流量突增。"""
        self._metrics_override.setdefault(service_name, {})["qps"] = float(qps)
        return {"status": "injected", "service": service_name, "qps": qps}

    def inject_error_rate_spike(self, service_name: str, error_rate: float = 0.15) -> dict[str, Any]:
        """注入错误率突增。"""
        self._metrics_override.setdefault(service_name, {})["error_rate"] = error_rate * 100
        return {"status": "injected", "service": service_name, "error_rate": error_rate}

    # ─────────────────────────────────────
    # 场景管理
    # ─────────────────────────────────────
    def set_scenario(self, name: str | None, data: dict | None = None) -> None:
        """设置当前活跃场景。"""
        self._active_scenario = name
        self._scenario_data = data or {}

    def get_scenario(self) -> str | None:
        return self._active_scenario

    def reset(self) -> None:
        """重置到基线状态。"""
        self._active_scenario = None
        self._scenario_data.clear()
        self._metrics_override.clear()
        self._changes.clear()
        self._alerts.clear()
