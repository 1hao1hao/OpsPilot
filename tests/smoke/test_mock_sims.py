"""Mock 模拟器冒烟测试 — PRD-06 §10。

通过 Mock 环境 HTTP API 验证各模拟器的查询和故障注入功能。
覆盖 K8s / DB / Redis / Kafka / Service 五类模拟器。
"""

from __future__ import annotations


# ── K8s 模拟器 ──────────────────────────────────────────────

class TestMockK8s:
    """K8s 模拟器冒烟测试。"""

    def test_list_deployments(self, mock_client):
        resp = mock_client.get("/k8s/deployments")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 4  # PRD-05 默认 4 个 Deployment

    def test_get_deployment(self, mock_client):
        resp = mock_client.get("/k8s/deployments/order-service")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "order-service"

    def test_get_pods(self, mock_client):
        resp = mock_client.get("/k8s/deployments/order-service/pods")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_list_events(self, mock_client):
        resp = mock_client.get("/k8s/events")
        assert resp.status_code == 200

    def test_inject_pod_restart(self, reset_mock, mock_client):
        resp = mock_client.post("/k8s/inject/pod-restart", json={
            "deployment_name": "order-service",
            "pod_index": 0,
        })
        assert resp.status_code == 200

    def test_inject_resource_pressure(self, reset_mock, mock_client):
        resp = mock_client.post("/k8s/inject/resource-pressure", json={
            "deployment_name": "order-service",
            "cpu_usage": 95.0,
            "memory_usage": 90.0,
        })
        assert resp.status_code == 200


# ── DB 模拟器 ────────────────────────────────────────────────

class TestMockDB:
    """MySQL 数据库模拟器冒烟测试。"""

    def test_get_metrics(self, mock_client):
        resp = mock_client.get("/db/mysql-prod-01/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "slave_delay" in data or "slave_delay_seconds" in data

    def test_get_topology(self, mock_client):
        resp = mock_client.get("/db/mysql-prod-01/topology")
        assert resp.status_code == 200

    def test_inject_slave_delay(self, reset_mock, mock_client):
        resp = mock_client.post("/db/mysql-prod-01/inject/slave-delay", json={
            "delay_seconds": 15.0,
        })
        assert resp.status_code == 200


# ── Redis 模拟器 ─────────────────────────────────────────────

class TestMockRedis:
    """Redis 缓存模拟器冒烟测试。"""

    def test_get_metrics(self, mock_client):
        resp = mock_client.get("/redis/redis-prod-01/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "used_memory_gb" in data or "used_memory" in data

    def test_get_hotkeys(self, mock_client):
        resp = mock_client.get("/redis/redis-prod-01/hotkeys")
        assert resp.status_code == 200

    def test_inject_memory_pressure(self, reset_mock, mock_client):
        resp = mock_client.post("/redis/redis-prod-01/inject/memory-pressure", json={
            "used_memory": 7.5,
        })
        assert resp.status_code == 200


# ── Kafka 模拟器 ─────────────────────────────────────────────

class TestMockKafka:
    """Kafka 消息队列模拟器冒烟测试。"""

    def test_get_consumer_lag(self, mock_client):
        resp = mock_client.get("/kafka/kafka-prod/topics/order-events/lag")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_lag" in data

    def test_get_metrics(self, mock_client):
        resp = mock_client.get("/kafka/kafka-prod/metrics")
        assert resp.status_code == 200

    def test_inject_consumer_offline(self, reset_mock, mock_client):
        resp = mock_client.post("/kafka/kafka-prod/inject/consumer-offline", json={
            "consumer": "consumer-1",
        })
        assert resp.status_code == 200


# ── Service 模拟器 ───────────────────────────────────────────

class TestMockService:
    """微服务调用链模拟器冒烟测试。"""

    def test_get_topology(self, mock_client):
        resp = mock_client.get("/service/order-service/topology")
        assert resp.status_code == 200
        data = resp.json()
        assert "service" in data or "name" in data

    def test_get_metrics(self, mock_client):
        resp = mock_client.get("/service/order-service/metrics/latency_ms")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["data_points"], list)
        assert len(data["data_points"]) > 0

    def test_get_logs(self, mock_client):
        resp = mock_client.get("/service/order-service/logs")
        assert resp.status_code == 200

    def test_get_traces(self, mock_client):
        resp = mock_client.get("/service/order-service/traces")
        assert resp.status_code == 200

    def test_inject_timeout(self, reset_mock, mock_client):
        resp = mock_client.post("/service/order-service/inject/timeout", json={
            "tp99_ms": 800,
        })
        assert resp.status_code == 200
