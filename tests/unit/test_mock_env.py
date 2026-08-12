"""Mock 环境模拟器单元测试 — K8s/DB/Redis/Kafka/Service/Alert Simulator。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：6 个模拟器的单元测试</td><td>PRD-05 §3-§6</td></tr>
</table>
@author DeepRCA Team
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import pytest

from deeprca.mock_env.k8s_simulator import K8sSimulator, PodPhase
from deeprca.mock_env.mysql_simulator import MySQLSimulator
from deeprca.mock_env.redis_simulator import RedisSimulator
from deeprca.mock_env.kafka_simulator import KafkaSimulator
from deeprca.mock_env.service_simulator import MicroserviceSimulator
from deeprca.mock_env.alert_simulator import AlertSimulator, SCENARIOS


# ─────────────────────────────────────
# K8s 模拟器
# ─────────────────────────────────────

class TestK8sSimulator:
    """K8s 集群模拟器测试。"""

    def setup_method(self):
        self.sim = K8sSimulator()

    def test_default_cluster_initialized(self):
        """默认集群应包含 4 个 Deployment。"""
        deployments = self.sim.list_deployments()
        assert len(deployments) == 4
        names = [d["name"] for d in deployments]
        assert "order-service" in names
        assert "payment-service" in names

    def test_deployment_has_pods(self):
        """Deployment 应包含 Pod。"""
        pods = self.sim.get_pods("order-service")
        assert pods is not None
        assert len(pods) == 3  # order-service 有 3 副本
        assert pods[0]["phase"] == "Running"

    def test_inject_pod_restart(self):
        """注入 Pod 重启应增加 restart_count。"""
        result = self.sim.inject_pod_restart("order-service", 0, "OOMKilled")
        assert result["status"] == "injected"
        assert result["restart_count"] == 1
        pods = self.sim.get_pods("order-service")
        assert pods[0]["restart_count"] == 1

    def test_inject_resource_pressure(self):
        """注入资源压力应修改 CPU/Memory 使用率。"""
        result = self.sim.inject_resource_pressure("order-service", cpu_usage=1.8, memory_usage=3600)
        assert result["status"] == "injected"
        pods = self.sim.get_pods("order-service")
        assert pods[0]["cpu_usage"] == 1.8

    def test_inject_pod_crash(self):
        """注入 Pod 崩溃应设置 phase 为 FAILED。"""
        result = self.sim.inject_pod_crash("order-service", 0)
        assert result["status"] == "crashed"
        pods = self.sim.get_pods("order-service")
        assert pods[0]["phase"] == "Failed"

    def test_scale_deployment(self):
        """扩缩容应调整副本数。"""
        result = self.sim.scale_deployment("order-service", 5)
        assert result["status"] == "scaled"
        assert result["new"] == 5
        pods = self.sim.get_pods("order-service")
        assert len(pods) == 5

    def test_events_recorded(self):
        """故障注入应记录事件。"""
        self.sim.inject_pod_restart("order-service", 0)
        events = self.sim.list_events()
        assert len(events) > 0
        assert events[0]["type"] == "Warning"

    def test_reset(self):
        """重置应恢复基线状态。"""
        self.sim.inject_pod_restart("order-service", 0)
        self.sim.reset()
        pods = self.sim.get_pods("order-service")
        assert pods[0]["restart_count"] == 0


# ─────────────────────────────────────
# MySQL 模拟器
# ─────────────────────────────────────

class TestMySQLSimulator:
    """MySQL 数据库模拟器测试。"""

    def setup_method(self):
        self.sim = MySQLSimulator()

    def test_default_state(self):
        """默认状态应正常。"""
        metrics = self.sim.get_metrics()
        assert metrics["active_connections"]["current"] == 80
        assert metrics["slave_delay_seconds"]["current"] == 0.5
        assert not metrics["slave_delay_seconds"]["exceeded"]

    def test_inject_slave_delay(self):
        """注入主从延迟应更新状态。"""
        result = self.sim.inject_slave_delay(15.0)
        assert result["status"] == "injected"
        metrics = self.sim.get_metrics(["slave_delay_seconds"])
        assert metrics["slave_delay_seconds"]["current"] == 15.0
        assert metrics["slave_delay_seconds"]["exceeded"]

    def test_inject_connection_pool(self):
        """注入连接池耗尽应更新 active/waiting。"""
        result = self.sim.inject_connection_pool_exhaustion(190)
        assert result["status"] == "injected"
        metrics = self.sim.get_metrics(["active_connections"])
        assert metrics["active_connections"]["current"] == 190
        assert metrics["active_connections"]["usage_ratio"] > 0.9

    def test_inject_slow_query(self):
        """注入慢查询应增加 slow_queries 列表。"""
        result = self.sim.inject_slow_query("SELECT * FROM orders", 800, 30)
        assert result["status"] == "injected"
        slow_log = self.sim.get_slow_log()
        assert len(slow_log) == 30
        assert slow_log[0]["duration_ms"] == 800

    def test_inject_lock_wait(self):
        """注入锁等待应增加 lock_waits。"""
        result = self.sim.inject_lock_wait(5)
        assert result["status"] == "injected"
        metrics = self.sim.get_metrics(["innodb_lock_waits"])
        assert metrics["innodb_lock_waits"]["current"] == 5

    def test_reset(self):
        """重置应恢复基线。"""
        self.sim.inject_slave_delay(15.0)
        self.sim.inject_slow_query(count=10)
        self.sim.reset()
        metrics = self.sim.get_metrics()
        assert metrics["slave_delay_seconds"]["current"] == 0.5
        assert metrics["slow_query_count"]["current"] == 0


# ─────────────────────────────────────
# Redis 模拟器
# ─────────────────────────────────────

class TestRedisSimulator:
    """Redis 缓存模拟器测试。"""

    def setup_method(self):
        self.sim = RedisSimulator()

    def test_default_state(self):
        """默认状态应正常。"""
        metrics = self.sim.get_metrics()
        assert metrics["used_memory"]["current_gb"] == 4.0
        assert metrics["hit_rate"]["current"] == 0.95
        assert metrics["evicted_keys"]["current"] == 0

    def test_inject_memory_pressure(self):
        """注入内存压力应触发 evicted_keys。"""
        result = self.sim.inject_memory_pressure(7.5)
        assert result["status"] == "injected"
        assert result["usage_ratio"] > 0.85
        assert result["evicted_keys"] > 0

    def test_inject_hit_rate_drop(self):
        """注入命中率下降应更新 hit_rate。"""
        result = self.sim.inject_hit_rate_drop(0.70)
        assert result["status"] == "injected"
        metrics = self.sim.get_metrics(["hit_rate"])
        assert metrics["hit_rate"]["current"] == 0.70
        assert metrics["hit_rate"]["drop_pp"] == 25.0

    def test_inject_hotkey(self):
        """注入热点 Key 应添加到 hotkeys 列表。"""
        result = self.sim.inject_hotkey("user:session:hot", 15000)
        assert result["status"] == "injected"
        hotkeys = self.sim.get_hotkeys()
        assert len(hotkeys) == 1
        assert hotkeys[0]["qps"] == 15000

    def test_reset(self):
        """重置应恢复基线。"""
        self.sim.inject_memory_pressure(7.5)
        self.sim.inject_hit_rate_drop(0.60)
        self.sim.reset()
        metrics = self.sim.get_metrics()
        assert metrics["used_memory"]["current_gb"] == 4.0
        assert metrics["hit_rate"]["current"] == 0.95


# ─────────────────────────────────────
# Kafka 模拟器
# ─────────────────────────────────────

class TestKafkaSimulator:
    """Kafka 消息队列模拟器测试。"""

    def setup_method(self):
        self.sim = KafkaSimulator()

    def test_default_state(self):
        """默认状态应正常。"""
        lag = self.sim.get_consumer_lag()
        assert lag["total_lag"] == 300  # 3 partitions * 100
        assert lag["produce_rate"] == 1000
        assert lag["consume_rate"] == 1000

    def test_inject_consumer_offline(self):
        """注入消费者离线应增加积压。"""
        result = self.sim.inject_consumer_offline()
        assert result["status"] == "injected"
        lag = self.sim.get_consumer_lag()
        assert lag["total_lag"] > 300  # 积压应增加
        assert lag["partitions"][2]["consumer"] is None

    def test_inject_rebalance(self):
        """注入 Rebalance 风暴应增加 rebalance_count。"""
        result = self.sim.inject_rebalance_storm(count=5)
        assert result["status"] == "injected"
        lag = self.sim.get_consumer_lag()
        assert lag["rebalance_events"] == 5

    def test_inject_consume_rate_drop(self):
        """注入消费速率下降应更新 consume_rate。"""
        result = self.sim.inject_consume_rate_drop(300)
        assert result["status"] == "injected"
        metrics = self.sim.get_metrics()
        assert metrics["consume_rate"] == 300

    def test_reset(self):
        """重置应恢复基线。"""
        self.sim.inject_consumer_offline()
        self.sim.reset()
        lag = self.sim.get_consumer_lag()
        assert lag["total_lag"] == 300


# ─────────────────────────────────────
# 微服务模拟器
# ─────────────────────────────────────

class TestMicroserviceSimulator:
    """微服务调用链模拟器测试。"""

    def setup_method(self):
        self.sim = MicroserviceSimulator()

    def test_topology(self):
        """拓扑应返回上下游关系。"""
        topo = self.sim.get_topology("order-service")
        assert topo["service"] == "order-service"
        assert len(topo["upstream"]) > 0
        assert len(topo["downstream"]) > 0

    def test_metrics_generation(self):
        """指标生成应返回时序数据。"""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        result = self.sim.get_metrics("order-service", "qps", start, end)
        assert result["service"] == "order-service"
        assert len(result["data_points"]) == 60
        assert "aggregation" in result

    def test_logs_generation(self):
        """日志生成应返回错误日志。"""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        result = self.sim.get_logs("order-service", start, end, limit=20)
        assert result["service"] == "order-service"
        assert len(result["logs"]) > 0

    def test_traces_generation(self):
        """调用链生成应返回 span 数据。"""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        result = self.sim.get_traces("order-service", start, end, limit=10)
        assert len(result["traces"]) > 0
        assert len(result["traces"][0]["spans"]) > 1

    def test_inject_timeout(self):
        """注入超时应设置 metrics_override。"""
        result = self.sim.inject_service_timeout("order-service", "mysql-prod-01", 800)
        assert result["status"] == "injected"
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        metrics = self.sim.get_metrics("order-service", "tp99", start, end)
        # 后半段应有异常值
        mid = len(metrics["data_points"]) // 2
        assert metrics["data_points"][mid]["value"] > 700

    def test_scenario_mode(self):
        """场景模式应在时间序列后半段注入异常。"""
        self.sim.set_scenario("db_slow_query")
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        metrics = self.sim.get_metrics("order-service", "tp99", start, end)
        mid = len(metrics["data_points"]) // 2
        assert metrics["data_points"][mid]["value"] > 700

    def test_reset(self):
        """重置应清除场景和 override。"""
        self.sim.set_scenario("pod_crash")
        self.sim.inject_traffic_spike("order-service", 5000)
        self.sim.reset()
        assert self.sim.get_scenario() is None


# ─────────────────────────────────────
# 告警与场景模拟器
# ─────────────────────────────────────

class TestAlertSimulator:
    """告警与场景模拟器测试。"""

    def setup_method(self):
        self.sim = AlertSimulator()

    def test_list_scenarios(self):
        """应列出 8 个场景。"""
        scenarios = self.sim.list_scenarios()
        assert len(scenarios) == 8

    def test_get_scenario_detail(self):
        """场景详情应包含必需字段。"""
        scenario = self.sim.get_scenario("db_slave_delay_timeout")
        assert scenario is not None
        assert "alert" in scenario
        assert "injections" in scenario
        assert "expected_root_cause" in scenario
        assert scenario["alert"]["alert_type"] == "timeout"

    def test_apply_scenario(self):
        """应用场景应执行故障注入。"""
        result = self.sim.apply_scenario("db_slave_delay_timeout")
        assert result["status"] == "applied"
        assert len(result["injection_results"]) == 3

    def test_apply_resets_first(self):
        """应用场景前应重置。"""
        # 先注入一些故障
        self.sim.mysql.inject_slave_delay(20.0)
        # 再应用场景
        self.sim.apply_scenario("oom_restart")
        # MySQL 应被重置（oom_restart 不涉及 DB 注入）
        metrics = self.sim.mysql.get_metrics(["slave_delay_seconds"])
        assert metrics["slave_delay_seconds"]["current"] == 0.5

    def test_generate_alert(self):
        """应从场景生成告警事件。"""
        alert = self.sim.generate_alert("kafka_consumer_lag")
        assert alert["alert_type"] == "resource"
        assert alert["service_name"] == "order-service"
        assert alert["severity"] == "P1"

    def test_all_scenarios_in_prd_matrix(self):
        """所有 PRD-05 §10 场景矩阵中的场景应存在。"""
        expected = {
            "db_slave_delay_timeout",
            "oom_restart",
            "kafka_consumer_lag",
            "change_induced_failure",
            "redis_memory_pressure",
            "traffic_spike_saturation",
            "rpc_circuit_breaker",
            "multi_dimension_anomaly",
        }
        assert expected.issubset(set(SCENARIOS.keys()))

    def test_reset_all(self):
        """重置应恢复所有模拟器。"""
        self.sim.apply_scenario("multi_dimension_anomaly")
        self.sim.reset()
        # 验证各模拟器已恢复
        assert self.sim.mysql.get_metrics(["slow_query_count"])["slow_query_count"]["current"] == 0
        assert self.sim.redis.get_metrics(["hit_rate"])["hit_rate"]["current"] == 0.95
        assert self.sim.kafka.get_consumer_lag()["total_lag"] == 300
