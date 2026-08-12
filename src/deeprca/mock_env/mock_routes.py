"""Mock 环境 API 路由 — 将 /api/v1/mock/* 端点接线到模拟器。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：Mock API 路由（K8s/DB/Redis/Kafka/Service/Scenario）</td><td>PRD-05 §8</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from deeprca.mock_env.alert_simulator import SCENARIOS, get_alert_simulator


def create_mock_router() -> APIRouter:
    """创建 Mock 环境 API 路由器。"""
    router = APIRouter(prefix="/api/v1/mock", tags=["Mock Environment"])
    sim = get_alert_simulator()

    # ─────────────────────────────────────
    # 健康检查 & 重置
    # ─────────────────────────────────────
    @router.get("/health")
    async def mock_health():
        """模拟环境健康检查。"""
        return {"status": "healthy", "simulators": ["k8s", "db", "redis", "kafka", "service", "alert"]}

    @router.post("/reset")
    async def mock_reset():
        """重置所有模拟器。"""
        sim.reset()
        return {"status": "reset"}

    # ─────────────────────────────────────
    # 场景管理
    # ─────────────────────────────────────
    @router.get("/scenarios")
    async def list_scenarios():
        """列出所有测试场景。"""
        return sim.list_scenarios()

    @router.get("/scenarios/{name}")
    async def get_scenario(name: str):
        """获取场景详情。"""
        scenario = sim.get_scenario(name)
        if not scenario:
            return JSONResponse(status_code=404, content={"message": f"Scenario '{name}' not found"})
        return scenario

    @router.post("/scenarios/{name}/apply")
    async def apply_scenario(name: str):
        """应用场景（执行故障注入）。"""
        if name not in SCENARIOS:
            return JSONResponse(status_code=404, content={"message": f"Scenario '{name}' not found"})
        result = sim.apply_scenario(name)
        return result

    @router.post("/scenarios/{name}/run")
    async def run_scenario_e2e(name: str):
        """端到端执行：注入 + 分析 + 验证。

        此端点执行以下流程:
        1. 重置模拟环境
        2. 应用故障注入
        3. 生成告警事件
        4. 通过 HTTP API 提交到 DeepRCA-Agent 分析
        5. 轮询等待分析完成
        6. 对比预期根因
        7. 返回验证报告
        """
        if name not in SCENARIOS:
            return JSONResponse(status_code=404, content={"message": f"Scenario '{name}' not found"})

        import asyncio

        import httpx

        from deeprca.config import get_settings

        scenario = SCENARIOS[name]
        # 1. 应用场景
        sim.apply_scenario(name)
        # 2. 生成告警
        alert = sim.generate_alert(name)

        # 3. 通过 HTTP API 提交分析（不再直接调用 graph）
        settings = get_settings()
        base_url = settings.agent_url.rstrip("/")

        try:
            async with httpx.AsyncClient(timeout=settings.analysis_timeout) as client:
                resp = await client.post(f"{base_url}/api/v1/analyze", json=alert)
                resp.raise_for_status()
                submit_result = resp.json()
                trace_id = submit_result["trace_id"]

                # 4. 轮询等待分析完成
                # 轮询超时设为 analysis_timeout 的 1.5 倍，留出缓冲避免慢速环境 false negative
                max_wait = int(settings.analysis_timeout * 1.5)
                waited = 0
                poll_interval = 2
                final_status = "running"
                while waited < max_wait:
                    await asyncio.sleep(poll_interval)
                    waited += poll_interval
                    sresp = await client.get(f"{base_url}/api/v1/analyze/{trace_id}/status")
                    if sresp.status_code == 200:
                        sdata = sresp.json()
                        final_status = sdata.get("status", "running")
                        if final_status in ("completed", "failed"):
                            break

                # 轮询超时后额外等待并重试一次，避免慢速环境 false negative
                if final_status == "running":
                    await asyncio.sleep(poll_interval * 3)
                    sresp = await client.get(f"{base_url}/api/v1/analyze/{trace_id}/status")
                    if sresp.status_code == 200:
                        final_status = sresp.json().get("status", "running")

                # 5. 获取分析结果
                if final_status == "running":
                    # 分析仍在进行中，返回 pending 而非 error
                    return JSONResponse(
                        status_code=202,
                        content={
                            "scenario": name,
                            "status": "pending",
                            "trace_id": trace_id,
                            "message": "Analysis still running after timeout, please retry later",
                        },
                    )
                rresp = await client.get(f"{base_url}/api/v1/analyze/{trace_id}/result")
                if rresp.status_code != 200:
                    return JSONResponse(
                        status_code=500,
                        content={
                            "scenario": name,
                            "status": "error",
                            "trace_id": trace_id,
                            "message": f"Failed to get result: HTTP {rresp.status_code}",
                        },
                    )
                result_data = rresp.json()
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={
                    "scenario": name,
                    "status": "error",
                    "message": f"HTTP API call failed: {exc}",
                },
            )

        # 6. 对比预期根因
        root_cause = result_data.get("root_cause")
        actual_conclusion = ""
        actual_confidence = 0.0
        if root_cause and isinstance(root_cause, dict):
            best = root_cause.get("best_candidate") or {}
            actual_conclusion = best.get("root_cause", "")
            actual_confidence = best.get("confidence", 0.0)

        expected = scenario["expected_root_cause"]
        expected_min = scenario["expected_confidence_min"]

        # 语义匹配（简化：关键词重叠率）
        root_cause_matched = _semantic_match(actual_conclusion, expected)

        return {
            "scenario": name,
            "status": "passed" if root_cause_matched and actual_confidence >= expected_min else "failed",
            "trace_id": trace_id,
            "actual_root_cause": actual_conclusion,
            "expected_root_cause": expected,
            "root_cause_matched": root_cause_matched,
            "actual_confidence": actual_confidence,
            "expected_confidence_min": expected_min,
            "confidence_passed": actual_confidence >= expected_min,
            "final_status": final_status,
        }

    # ─────────────────────────────────────
    # K8s
    # ─────────────────────────────────────
    @router.get("/k8s/deployments")
    async def k8s_list_deployments():
        return sim.k8s.list_deployments()

    @router.get("/k8s/deployments/{name}")
    async def k8s_get_deployment(name: str):
        result = sim.k8s.get_deployment(name)
        if not result:
            return JSONResponse(status_code=404, content={"message": f"Deployment '{name}' not found"})
        return result

    @router.get("/k8s/deployments/{name}/pods")
    async def k8s_get_pods(name: str):
        result = sim.k8s.get_pods(name)
        if result is None:
            return JSONResponse(status_code=404, content={"message": f"Deployment '{name}' not found"})
        return result

    @router.get("/k8s/events")
    async def k8s_events():
        return sim.k8s.list_events()

    @router.post("/k8s/inject/pod-restart")
    async def k8s_inject_pod_restart(body: dict[str, Any]):
        return sim.k8s.inject_pod_restart(
            deployment_name=body.get("deployment_name", "order-service"),
            pod_index=body.get("pod_index", 0),
            reason=body.get("reason", "OOMKilled"),
        )

    @router.post("/k8s/inject/resource-pressure")
    async def k8s_inject_resource_pressure(body: dict[str, Any]):
        return sim.k8s.inject_resource_pressure(
            deployment_name=body.get("deployment_name", "order-service"),
            cpu_usage=body.get("cpu_usage"),
            memory_usage=body.get("memory_usage"),
        )

    @router.post("/k8s/inject/pod-crash")
    async def k8s_inject_pod_crash(body: dict[str, Any]):
        return sim.k8s.inject_pod_crash(
            deployment_name=body.get("deployment_name", "order-service"),
            pod_index=body.get("pod_index", 0),
        )

    @router.post("/k8s/scale")
    async def k8s_scale(body: dict[str, Any]):
        return sim.k8s.scale_deployment(
            deployment_name=body.get("deployment_name", "order-service"),
            replicas=body.get("replicas", 1),
        )

    # ─────────────────────────────────────
    # DB (MySQL)
    # ─────────────────────────────────────
    @router.get("/db/{instance}/metrics")
    async def db_metrics(instance: str, metrics: str = ""):
        metric_names = metrics.split(",") if metrics else None
        return sim.mysql.get_metrics(metric_names)

    @router.get("/db/{instance}/slow-log")
    async def db_slow_log(instance: str):
        return sim.mysql.get_slow_log()

    @router.get("/db/{instance}/topology")
    async def db_topology(instance: str):
        return sim.mysql.get_topology()

    @router.post("/db/{instance}/inject/slave-delay")
    async def db_inject_slave_delay(instance: str, body: dict[str, Any]):
        return sim.mysql.inject_slave_delay(
            delay_seconds=body.get("delay_seconds", 15.0),
            slave_index=body.get("slave_index", 0),
        )

    @router.post("/db/{instance}/inject/connection-pool")
    async def db_inject_connection_pool(instance: str, body: dict[str, Any]):
        return sim.mysql.inject_connection_pool_exhaustion(
            active=body.get("active", 180),
        )

    @router.post("/db/{instance}/inject/slow-query")
    async def db_inject_slow_query(instance: str, body: dict[str, Any]):
        return sim.mysql.inject_slow_query(
            sql=body.get("sql", "SELECT * FROM orders"),
            duration_ms=body.get("duration_ms", 800),
            count=body.get("count", 1),
        )

    @router.post("/db/{instance}/inject/lock-wait")
    async def db_inject_lock_wait(instance: str, body: dict[str, Any]):
        return sim.mysql.inject_lock_wait(
            count=body.get("count", 5),
        )

    # ─────────────────────────────────────
    # Redis
    # ─────────────────────────────────────
    @router.get("/redis/{instance}/metrics")
    async def redis_metrics(instance: str, metrics: str = ""):
        metric_names = metrics.split(",") if metrics else None
        return sim.redis.get_metrics(metric_names)

    @router.get("/redis/{instance}/hotkeys")
    async def redis_hotkeys(instance: str):
        return sim.redis.get_hotkeys()

    @router.get("/redis/{instance}/topology")
    async def redis_topology(instance: str):
        return sim.redis.get_topology()

    @router.post("/redis/{instance}/inject/memory-pressure")
    async def redis_inject_memory(instance: str, body: dict[str, Any]):
        return sim.redis.inject_memory_pressure(
            used_memory=body.get("used_memory", 7.5),
        )

    @router.post("/redis/{instance}/inject/hit-rate-drop")
    async def redis_inject_hit_rate(instance: str, body: dict[str, Any]):
        return sim.redis.inject_hit_rate_drop(
            hit_rate=body.get("hit_rate", 0.70),
        )

    @router.post("/redis/{instance}/inject/hotkey")
    async def redis_inject_hotkey(instance: str, body: dict[str, Any]):
        return sim.redis.inject_hotkey(
            key=body.get("key", "user:session:hot"),
            qps=body.get("qps", 15000),
        )

    @router.post("/redis/{instance}/inject/bigkey")
    async def redis_inject_bigkey(instance: str, body: dict[str, Any]):
        return sim.redis.inject_bigkey(
            key=body.get("key", "order:cache:batch"),
            size_mb=body.get("size_mb", 15),
        )

    # ─────────────────────────────────────
    # Kafka
    # ─────────────────────────────────────
    @router.get("/kafka/{cluster}/topics/{topic}/lag")
    async def kafka_lag(cluster: str, topic: str, group: str = "order-consumer-group"):
        return sim.kafka.get_consumer_lag(topic=topic, group=group)

    @router.get("/kafka/{cluster}/metrics")
    async def kafka_metrics(cluster: str):
        return sim.kafka.get_metrics()

    @router.post("/kafka/{cluster}/inject/consumer-offline")
    async def kafka_inject_consumer_offline(cluster: str, body: dict[str, Any]):
        return sim.kafka.inject_consumer_offline(
            topic=body.get("topic", "order-events"),
            group=body.get("group", "order-consumer-group"),
            consumer=body.get("consumer", "consumer-3"),
        )

    @router.post("/kafka/{cluster}/inject/rebalance")
    async def kafka_inject_rebalance(cluster: str, body: dict[str, Any]):
        return sim.kafka.inject_rebalance_storm(
            topic=body.get("topic", "order-events"),
            group=body.get("group", "order-consumer-group"),
            count=body.get("count", 5),
        )

    @router.post("/kafka/{cluster}/inject/consume-rate-drop")
    async def kafka_inject_consume_rate(cluster: str, body: dict[str, Any]):
        return sim.kafka.inject_consume_rate_drop(
            rate=body.get("rate", 300),
        )

    # ─────────────────────────────────────
    # 微服务
    # ─────────────────────────────────────
    @router.get("/service/{name}/topology")
    async def service_topology(name: str, depth: int = 2):
        return sim.service.get_topology(name, depth)

    @router.get("/service/{name}/metrics/{metric}")
    async def service_metrics(name: str, metric: str, start_time: str = "", end_time: str = ""):
        if not start_time or not end_time:
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            start_time = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            end_time = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        return sim.service.get_metrics(name, metric, start_time, end_time)

    @router.get("/service/{name}/traces")
    async def service_traces(name: str, start_time: str = "", end_time: str = "", limit: int = 50):
        if not start_time or not end_time:
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            start_time = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            end_time = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        return sim.service.get_traces(name, start_time, end_time, limit=limit)

    @router.get("/service/{name}/logs")
    async def service_logs(name: str, start_time: str = "", end_time: str = "", level: str = "ERROR", keyword: str = "", limit: int = 100):
        if not start_time or not end_time:
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            start_time = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            end_time = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        return sim.service.get_logs(name, start_time, end_time, level=level, keyword=keyword, limit=limit)

    @router.get("/service/{name}/changes")
    async def service_changes(name: str, time_window: int = 30):
        """获取微服务最近变更记录。"""
        return sim.service.get_changes(name, time_window)

    @router.get("/service/{name}/alerts")
    async def service_alerts(name: str, time_window: int = 30):
        """获取微服务关联告警。"""
        return sim.service.get_alerts(name, time_window)

    @router.post("/service/{name}/inject/timeout")
    async def service_inject_timeout(name: str, body: dict[str, Any]):
        return sim.service.inject_service_timeout(
            service_name=name,
            downstream=body.get("downstream", ""),
            tp99_ms=body.get("tp99_ms", 800),
        )

    @router.post("/service/{name}/inject/traffic-spike")
    async def service_inject_traffic(name: str, body: dict[str, Any]):
        return sim.service.inject_traffic_spike(
            service_name=name,
            qps=body.get("qps", 5000),
        )

    @router.post("/service/{name}/inject/error-rate")
    async def service_inject_error_rate(name: str, body: dict[str, Any]):
        return sim.service.inject_error_rate_spike(
            service_name=name,
            error_rate=body.get("error_rate", 0.15),
        )

    return router


def _semantic_match(actual: str, expected: str) -> bool:
    """简化的语义匹配 — 基于关键词重叠率。"""
    if not actual or not expected:
        return False
    # 提取关键词
    import re
    actual_words = set(re.findall(r"[\w\u4e00-\u9fff]+", actual.lower()))
    expected_words = set(re.findall(r"[\w\u4e00-\u9fff]+", expected.lower()))
    if not expected_words:
        return False
    overlap = len(actual_words & expected_words) / len(expected_words)
    # 短词场景（expected_words <= 3）要求更高重叠率，避免 1 词匹配即达标的假阳性
    threshold = 0.6 if len(expected_words) <= 3 else 0.5
    return overlap >= threshold
