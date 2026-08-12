"""告警与场景模拟器 — 预设故障场景，支持故障注入和端到端验证。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：8 个预设场景 + 故障注入编排</td><td>PRD-05 §6, §10</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from deeprca.mock_env.k8s_simulator import K8sSimulator
from deeprca.mock_env.kafka_simulator import KafkaSimulator
from deeprca.mock_env.mysql_simulator import MySQLSimulator
from deeprca.mock_env.redis_simulator import RedisSimulator
from deeprca.mock_env.service_simulator import MicroserviceSimulator


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


# ─────────────────────────────────────
# 预设测试场景
# ─────────────────────────────────────

SCENARIOS: dict[str, dict[str, Any]] = {
    "db_slave_delay_timeout": {
        "name": "DB 主从延迟超时",
        "description": "order-service TP99 延迟突增至 800ms",
        "severity": "P1",
        "alert_type": "timeout",
        "service_name": "order-service",
        "injections": [
            {"target": "db", "action": "inject_slave_delay", "params": {"delay_seconds": 15.0}},
            {"target": "db", "action": "inject_slow_query", "params": {"sql": "SELECT * FROM orders", "duration_ms": 800, "count": 30}},
            {"target": "service", "action": "inject_service_timeout", "params": {"service_name": "order-service", "downstream": "mysql-prod-01", "tp99_ms": 800}},
        ],
        "expected_root_cause": "数据库主从延迟导致 order-service 查询超时",
        "expected_confidence_min": 0.85,
        "key_metrics": ["tp99", "error_rate"],
        "key_logs": ["SQL execution timeout", "HikariPool", "Connection is not available"],
    },
    "oom_restart": {
        "name": "OOM 重启",
        "description": "order-service 错误率突增至 15%",
        "severity": "P0",
        "alert_type": "error_rate",
        "service_name": "order-service",
        "injections": [
            {"target": "k8s", "action": "inject_pod_restart", "params": {"deployment_name": "order-service", "pod_index": 0, "reason": "OOMKilled"}},
            {"target": "service", "action": "inject_error_rate_spike", "params": {"service_name": "order-service", "error_rate": 0.15}},
        ],
        "expected_root_cause": "服务内存溢出导致 Pod 被 Kill 并重启",
        "expected_confidence_min": 0.9,
        "key_metrics": ["error_rate", "cpu_usage"],
        "key_logs": ["OutOfMemoryError", "CrashLoopBackOff", "OOMKilled"],
    },
    "kafka_consumer_lag": {
        "name": "Kafka 消费积压",
        "description": "order-service Kafka 消费积压 50000+",
        "severity": "P1",
        "alert_type": "resource",
        "service_name": "order-service",
        "injections": [
            {"target": "kafka", "action": "inject_consumer_offline", "params": {"topic": "order-events", "group": "order-consumer-group", "consumer": "consumer-3"}},
            {"target": "kafka", "action": "inject_consume_rate_drop", "params": {"rate": 300}},
        ],
        "expected_root_cause": "Kafka 消费者离线导致消息积压",
        "expected_confidence_min": 0.85,
        "key_metrics": ["error_rate"],
        "key_logs": ["Kafka consumer lag", "heartbeat timeout", "rebalance"],
    },
    "change_induced_failure": {
        "name": "配置变更导致故障",
        "description": "order-service TP99 延迟突增至 600ms",
        "severity": "P1",
        "alert_type": "timeout",
        "service_name": "order-service",
        "injections": [
            {"target": "db", "action": "inject_connection_pool_exhaustion", "params": {"active": 180}},
        ],
        "changes": [
            {"change_id": "chg-mock-001", "type": "config", "description": "调整 DB 连接池 innodb_buffer_pool_size 从 4G 到 8G",
             "operator": "testuser", "risk_level": "medium", "related_service": "mysql-prod-01"},
        ],
        "expected_root_cause": "配置变更导致连接池参数异常",
        "expected_confidence_min": 0.8,
        "key_metrics": ["tp99", "error_rate"],
        "key_logs": ["Connection pool", "timeout"],
    },
    "redis_memory_pressure": {
        "name": "Redis 内存压力",
        "description": "order-service 缓存命中率下降，错误率上升",
        "severity": "P2",
        "alert_type": "error_rate",
        "service_name": "order-service",
        "injections": [
            {"target": "redis", "action": "inject_memory_pressure", "params": {"used_memory": 7.5}},
            {"target": "redis", "action": "inject_hit_rate_drop", "params": {"hit_rate": 0.70}},
            {"target": "redis", "action": "inject_bigkey", "params": {"key": "order:cache:batch", "size_mb": 15}},
        ],
        "expected_root_cause": "Redis 内存压力导致缓存命中率下降",
        "expected_confidence_min": 0.8,
        "key_metrics": ["error_rate", "tp99"],
        "key_logs": ["Redis command timeout", "JedisConnectionException"],
    },
    "traffic_spike_saturation": {
        "name": "流量突增资源饱和",
        "description": "order-service QPS 突增至 5000+，资源饱和",
        "severity": "P1",
        "alert_type": "timeout",
        "service_name": "order-service",
        "injections": [
            {"target": "service", "action": "inject_traffic_spike", "params": {"service_name": "order-service", "qps": 5000}},
            {"target": "k8s", "action": "inject_resource_pressure", "params": {"deployment_name": "order-service", "cpu_usage": 1.8, "memory_usage": 3600}},
        ],
        "expected_root_cause": "流量突增导致资源饱和",
        "expected_confidence_min": 0.8,
        "key_metrics": ["qps", "tp99", "cpu_usage"],
        "key_logs": ["Request queue full", "Rate limit exceeded", "Thread pool exhausted"],
    },
    "rpc_circuit_breaker": {
        "name": "RPC 熔断触发",
        "description": "order-service 调用 payment-service 触发熔断",
        "severity": "P1",
        "alert_type": "error_rate",
        "service_name": "order-service",
        "injections": [
            {"target": "service", "action": "inject_service_timeout", "params": {"service_name": "order-service", "downstream": "payment-service", "tp99_ms": 1000}},
            {"target": "service", "action": "inject_error_rate_spike", "params": {"service_name": "order-service", "error_rate": 0.10}},
        ],
        "expected_root_cause": "下游 RPC 服务异常触发熔断",
        "expected_confidence_min": 0.85,
        "key_metrics": ["error_rate", "tp99"],
        "key_logs": ["Circuit breaker", "Connection refused", "timeout"],
    },
    "multi_dimension_anomaly": {
        "name": "多维度异常共振",
        "description": "DB + Redis + K8s 同时异常",
        "severity": "P0",
        "alert_type": "error_rate",
        "service_name": "order-service",
        "injections": [
            {"target": "db", "action": "inject_slow_query", "params": {"sql": "SELECT * FROM orders JOIN...", "duration_ms": 1200, "count": 50}},
            {"target": "redis", "action": "inject_hit_rate_drop", "params": {"hit_rate": 0.60}},
            {"target": "k8s", "action": "inject_pod_crash", "params": {"deployment_name": "order-service", "pod_index": 1}},
        ],
        "expected_root_cause": "多维度异常共振导致服务不可用",
        "expected_confidence_min": 0.75,
        "key_metrics": ["error_rate", "tp99", "cpu_usage"],
        "key_logs": ["SQL execution timeout", "Redis timeout", "Pod crashed"],
    },
}


class AlertSimulator:
    """告警事件模拟器。

    管理预设测试场景，支持故障注入编排和端到端验证。
    """

    def __init__(self) -> None:
        self.k8s = K8sSimulator()
        self.mysql = MySQLSimulator()
        self.redis = RedisSimulator()
        self.kafka = KafkaSimulator()
        self.service = MicroserviceSimulator()

    def list_scenarios(self) -> list[dict[str, Any]]:
        """列出所有测试场景。"""
        return [
            {
                "name": name,
                "description": s["description"],
                "severity": s["severity"],
                "alert_type": s["alert_type"],
                "service_name": s["service_name"],
            }
            for name, s in SCENARIOS.items()
        ]

    def get_scenario(self, scenario_name: str) -> dict[str, Any] | None:
        """获取场景详情。"""
        scenario = SCENARIOS.get(scenario_name)
        if not scenario:
            return None
        result = json.loads(json.dumps(scenario))  # deep copy
        now = _now_iso()
        result["alert"] = {
            "alert_id": f"alert-{scenario_name}-{now[-8:].replace(':', '')}",
            "service_name": scenario["service_name"],
            "alert_type": scenario["alert_type"],
            "severity": scenario["severity"],
            "timestamp": now,
            "description": scenario["description"],
            "labels": {"cluster": "prod-cluster-01", "env": "production"},
        }
        for change in result.get("changes", []):
            if not change.get("timestamp"):
                change["timestamp"] = now
        return result

    def apply_scenario(self, scenario_name: str) -> dict[str, Any]:
        """应用场景 — 执行故障注入。

        重置所有模拟器到基线状态，然后按场景配置执行注入。
        """
        scenario = SCENARIOS.get(scenario_name)
        if not scenario:
            return {"error": f"unknown scenario: {scenario_name}"}

        # 重置
        self.reset()

        # 设置服务模拟器场景
        self.service.set_scenario(scenario_name)

        # 执行注入
        injection_results: list[dict[str, Any]] = []
        for inj in scenario.get("injections", []):
            target = inj["target"]
            action = inj["action"]
            params = inj.get("params", {})
            result = self._execute_injection(target, action, params)
            injection_results.append({"target": target, "action": action, "result": result})

        # 设置变更记录（如果有）
        if scenario.get("changes"):
            self.service.set_scenario(scenario_name, {"changes": scenario["changes"]})

        return {
            "scenario": scenario_name,
            "status": "applied",
            "injection_results": injection_results,
            "applied_at": _now_iso(),
        }

    def _execute_injection(self, target: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """执行单个故障注入。"""
        simulator_map = {
            "k8s": self.k8s,
            "db": self.mysql,
            "redis": self.redis,
            "kafka": self.kafka,
            "service": self.service,
        }
        sim = simulator_map.get(target)
        if not sim:
            return {"error": f"unknown target: {target}"}
        method = getattr(sim, action, None)
        if not method:
            return {"error": f"unknown action: {action}"}
        try:
            return method(**params)
        except TypeError:
            return {"error": f"parameter mismatch for {target}.{action}"}

    def generate_alert(self, scenario_name: str) -> dict[str, Any]:
        """从场景生成告警事件。"""
        scenario = SCENARIOS.get(scenario_name)
        if not scenario:
            return {"error": f"unknown scenario: {scenario_name}"}
        now = _now_iso()
        return {
            "alert_id": f"alert-{scenario_name}-{now[-8:].replace(':', '')}",
            "service_name": scenario["service_name"],
            "alert_type": scenario["alert_type"],
            "severity": scenario["severity"],
            "timestamp": now,
            "description": scenario["description"],
            "labels": {"cluster": "prod-cluster-01", "env": "production"},
        }

    def reset(self) -> None:
        """重置所有模拟器到基线状态。"""
        self.k8s.reset()
        self.mysql.reset()
        self.redis.reset()
        self.kafka.reset()
        self.service.reset()


# 全局单例
_alert_simulator: AlertSimulator | None = None


def get_alert_simulator() -> AlertSimulator:
    """获取全局 AlertSimulator 单例。"""
    global _alert_simulator
    if _alert_simulator is None:
        _alert_simulator = AlertSimulator()
    return _alert_simulator
