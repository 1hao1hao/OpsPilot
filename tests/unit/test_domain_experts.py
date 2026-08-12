"""PRD-03 领域专家子 Agent 单元测试。

覆盖 §10 测试要点：
- DB Expert: 主从延迟 + 慢查询突增 → 置信度 ≥ 0.8
- Redis Expert: 内存使用率 90% + eviction → 置信度 ≥ 0.6
- Mafka Expert: 消费者离线 + 积压增长 → 置信度 ≥ 0.6
- RPC Expert: 失败率 10% + 熔断触发 → 置信度 ≥ 0.8
- Change Agent: 变更在告警前 20 分钟 → 置信度 ≥ 0.7
- ErrorLog Agent: OOM 错误 100 次/min → 置信度 ≥ 0.7
- 并发调度: 6 维度同时执行 → 总耗时 ≤ 30s，部分失败不影响整体
- 注册表: get_expert_agent 正确返回实例

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.3.0</td><td>初始创建：PRD-03 领域专家测试</td><td>REQ: PRD-03 §10</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from deeprca.config import Settings
from deeprca.graph.subgraphs import (
    DBExpertAgent,
    MafkaExpertAgent,
    RedisExpertAgent,
    RPCExpertAgent,
    dispatch_to_experts,
    get_expert_agent,
)
from deeprca.models import SubAgentResult


@pytest.fixture
def real_api_settings():
    """返回 mock_env_enabled=False 的 Settings，用于测试 httpx 外部 API 路径。"""
    return Settings(
        app_env="test",
        mock_env_enabled=False,
        mock_monitor_api="http://localhost:8002",
    )


# ─────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────

def _mock_httpx_response(json_data: dict):
    """创建 mock httpx.Response。"""
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def _mock_async_client(json_data: dict | Exception):
    """创建 mock httpx.AsyncClient 上下文管理器。"""
    mock_client = AsyncMock()
    if isinstance(json_data, Exception):
        mock_client.get.side_effect = json_data
    else:
        mock_client.get.return_value = _mock_httpx_response(json_data)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ─────────────────────────────────────────────
# 注册表测试
# ─────────────────────────────────────────────

class TestExpertRegistry:
    """PRD-03 §2.2 注册表测试。"""

    def test_get_db_expert(self):
        agent = get_expert_agent("db")
        assert isinstance(agent, DBExpertAgent)
        assert agent.agent_name == "db_expert"

    def test_get_redis_expert(self):
        agent = get_expert_agent("redis")
        assert isinstance(agent, RedisExpertAgent)
        assert agent.agent_name == "redis_expert"

    def test_get_mafka_expert(self):
        agent = get_expert_agent("mafka")
        assert isinstance(agent, MafkaExpertAgent)
        assert agent.agent_name == "mafka_expert"

    def test_get_rpc_expert(self):
        agent = get_expert_agent("rpc")
        assert isinstance(agent, RPCExpertAgent)
        assert agent.agent_name == "rpc_expert"

    def test_unknown_domain_raises(self):
        with pytest.raises(ValueError, match="未知的领域专家"):
            get_expert_agent("unknown")

    def test_trigger_keywords(self):
        db = get_expert_agent("db")
        assert "mysql" in db.trigger_keywords
        assert "慢查询" in db.trigger_keywords

        redis = get_expert_agent("redis")
        assert "redis" in redis.trigger_keywords
        assert "缓存" in redis.trigger_keywords

    def test_should_trigger(self):
        db = get_expert_agent("db")
        findings_with_db = [{"message": "mysql connection pool exhausted"}]
        assert db.should_trigger(findings_with_db) is True

        findings_without_db = [{"message": "cpu usage high"}]
        assert db.should_trigger(findings_without_db) is False


# ─────────────────────────────────────────────
# DB Expert 测试
# ─────────────────────────────────────────────

class TestDBExpert:
    """PRD-03 §10: 主从延迟 + 慢查询突增 → 置信度 ≥ 0.8。"""

    @pytest.mark.asyncio
    async def test_slow_query_and_replication_lag(self, real_api_settings):
        """慢查询数 60 + 主从延迟 35s → 置信度 ≥ 0.8。"""
        mock_metrics = {
            "slow_query_count": 60,
            "active_connections": 100,
            "max_connections": 200,
            "lock_wait_count": 0,
            "replication_lag_seconds": 35.0,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.db_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.db_expert.httpx.AsyncClient", return_value=mock_client):
            agent = DBExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        assert result.agent_name == "db_expert"
        assert result.dimension == "database"
        assert result.confidence >= 0.8
        assert len(result.findings) >= 2
        finding_types = {f["type"] for f in result.findings}
        assert "slow_query" in finding_types
        assert "replication_lag" in finding_types

    @pytest.mark.asyncio
    async def test_connection_pool_high(self, real_api_settings):
        """连接池使用率 > 80% → 发现 connection_pool 异常。"""
        mock_metrics = {
            "slow_query_count": 5,
            "active_connections": 180,
            "max_connections": 200,
            "lock_wait_count": 0,
            "replication_lag_seconds": 0.5,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.db_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.db_expert.httpx.AsyncClient", return_value=mock_client):
            agent = DBExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        assert result.confidence >= 0.6
        finding_types = {f["type"] for f in result.findings}
        assert "connection_pool" in finding_types

    @pytest.mark.asyncio
    async def test_collect_error_degrades_gracefully(self, real_api_settings):
        """采集失败 → confidence=0.0, error 不为空。"""
        mock_client = _mock_async_client(ConnectionError("connection refused"))

        with patch("deeprca.graph.subgraphs.db_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.db_expert.httpx.AsyncClient", return_value=mock_client):
            agent = DBExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        assert result.confidence == 0.0
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_no_anomaly(self, real_api_settings):
        """无异常指标 → confidence ≤ 0.3。"""
        mock_metrics = {
            "slow_query_count": 2,
            "active_connections": 50,
            "max_connections": 200,
            "lock_wait_count": 0,
            "replication_lag_seconds": 0.1,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.db_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.db_expert.httpx.AsyncClient", return_value=mock_client):
            agent = DBExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        assert result.confidence <= 0.3
        assert len(result.findings) == 0

    @pytest.mark.asyncio
    async def test_lock_wait_critical(self, real_api_settings):
        """锁等待 > 5 → 发现 critical 级别 lock_wait。"""
        mock_metrics = {
            "slow_query_count": 3,
            "active_connections": 50,
            "max_connections": 200,
            "lock_wait_count": 8,
            "replication_lag_seconds": 0.1,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.db_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.db_expert.httpx.AsyncClient", return_value=mock_client):
            agent = DBExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        finding_types = {f["type"] for f in result.findings}
        assert "lock_wait" in finding_types
        lock_finding = next(f for f in result.findings if f["type"] == "lock_wait")
        assert lock_finding["severity"] == "critical"


# ─────────────────────────────────────────────
# Redis Expert 测试
# ─────────────────────────────────────────────

class TestRedisExpert:
    """PRD-03 §10: 内存使用率 90% + eviction → 置信度 ≥ 0.6。"""

    @pytest.mark.asyncio
    async def test_memory_high_and_low_hit_rate(self, real_api_settings):
        """内存 92% + 命中率 65% → 置信度 ≥ 0.8。"""
        mock_metrics = {
            "memory_usage_percent": 92.0,
            "hit_rate_percent": 65.0,
            "connected_clients": 100,
            "max_clients": 200,
            "big_keys_count": 0,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.redis_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.redis_expert.httpx.AsyncClient", return_value=mock_client):
            agent = RedisExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        assert result.agent_name == "redis_expert"
        assert result.dimension == "redis"
        assert result.confidence >= 0.8
        finding_types = {f["type"] for f in result.findings}
        assert "memory" in finding_types
        assert "hit_rate" in finding_types

    @pytest.mark.asyncio
    async def test_big_key_detected(self, real_api_settings):
        """检测到大 key → 发现 big_key 异常。"""
        mock_metrics = {
            "memory_usage_percent": 50.0,
            "hit_rate_percent": 95.0,
            "connected_clients": 50,
            "max_clients": 200,
            "big_keys_count": 3,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.redis_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.redis_expert.httpx.AsyncClient", return_value=mock_client):
            agent = RedisExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        finding_types = {f["type"] for f in result.findings}
        assert "big_key" in finding_types

    @pytest.mark.asyncio
    async def test_connection_high(self, real_api_settings):
        """连接数 > 80% → 发现 connection 异常。"""
        mock_metrics = {
            "memory_usage_percent": 50.0,
            "hit_rate_percent": 95.0,
            "connected_clients": 170,
            "max_clients": 200,
            "big_keys_count": 0,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.redis_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.redis_expert.httpx.AsyncClient", return_value=mock_client):
            agent = RedisExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        finding_types = {f["type"] for f in result.findings}
        assert "connection" in finding_types


# ─────────────────────────────────────────────
# Mafka Expert 测试
# ─────────────────────────────────────────────

class TestMafkaExpert:
    """PRD-03 §10: 消费者离线 + 积压增长 → 置信度 ≥ 0.6。"""

    @pytest.mark.asyncio
    async def test_consumer_lag_and_rate_mismatch(self, real_api_settings):
        """消费 lag=50000 + 速率不匹配 → 置信度 ≥ 0.8。"""
        mock_metrics = {
            "consumer_lag": 50000,
            "produce_rate": 1000.0,
            "consume_rate": 200.0,
            "rebalance_count": 0,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.mafka_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.mafka_expert.httpx.AsyncClient", return_value=mock_client):
            agent = MafkaExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        assert result.agent_name == "mafka_expert"
        assert result.dimension == "message_queue"
        assert result.confidence >= 0.8
        finding_types = {f["type"] for f in result.findings}
        assert "consumer_lag" in finding_types
        assert "rate_mismatch" in finding_types

    @pytest.mark.asyncio
    async def test_rebalance_detected(self, real_api_settings):
        """检测到 rebalance → 发现 rebalance 事件。"""
        mock_metrics = {
            "consumer_lag": 100,
            "produce_rate": 100.0,
            "consume_rate": 100.0,
            "rebalance_count": 3,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.mafka_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.mafka_expert.httpx.AsyncClient", return_value=mock_client):
            agent = MafkaExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        finding_types = {f["type"] for f in result.findings}
        assert "rebalance" in finding_types

    @pytest.mark.asyncio
    async def test_critical_lag(self, real_api_settings):
        """lag > 100000 → critical 级别。"""
        mock_metrics = {
            "consumer_lag": 150000,
            "produce_rate": 100.0,
            "consume_rate": 100.0,
            "rebalance_count": 0,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.mafka_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.mafka_expert.httpx.AsyncClient", return_value=mock_client):
            agent = MafkaExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        lag_finding = next(f for f in result.findings if f["type"] == "consumer_lag")
        assert lag_finding["severity"] == "critical"


# ─────────────────────────────────────────────
# RPC Expert 测试
# ─────────────────────────────────────────────

class TestRPCExpert:
    """PRD-03 §10: 失败率 10% + 熔断触发 → 置信度 ≥ 0.8。"""

    @pytest.mark.asyncio
    async def test_high_failure_rate_and_rt_spike(self, real_api_settings):
        """失败率 12% + RT 突变 4 倍 → 置信度 ≥ 0.8。"""
        mock_metrics = {
            "failure_rate_percent": 12.0,
            "avg_rt_ms": 400.0,
            "baseline_rt_ms": 100.0,
            "timeout_count": 15,
            "call_volume": 1000,
            "baseline_volume": 1000,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.rpc_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.rpc_expert.httpx.AsyncClient", return_value=mock_client):
            agent = RPCExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        assert result.agent_name == "rpc_expert"
        assert result.dimension == "rpc"
        assert result.confidence >= 0.8
        finding_types = {f["type"] for f in result.findings}
        assert "failure_rate" in finding_types
        assert "rt_spike" in finding_types

    @pytest.mark.asyncio
    async def test_timeout_exceeded(self, real_api_settings):
        """超时次数 > 10 → 发现 timeout 异常。"""
        mock_metrics = {
            "failure_rate_percent": 2.0,
            "avg_rt_ms": 80.0,
            "baseline_rt_ms": 100.0,
            "timeout_count": 20,
            "call_volume": 500,
            "baseline_volume": 500,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.rpc_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.rpc_expert.httpx.AsyncClient", return_value=mock_client):
            agent = RPCExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        finding_types = {f["type"] for f in result.findings}
        assert "timeout" in finding_types

    @pytest.mark.asyncio
    async def test_call_volume_spike(self, real_api_settings):
        """调用量 > 基线 3 倍 → 发现 call_volume_spike 异常。"""
        mock_metrics = {
            "failure_rate_percent": 2.0,
            "avg_rt_ms": 80.0,
            "baseline_rt_ms": 100.0,
            "timeout_count": 0,
            "call_volume": 5000,
            "baseline_volume": 1000,
        }
        mock_client = _mock_async_client(mock_metrics)

        with patch("deeprca.graph.subgraphs.rpc_expert.get_settings", return_value=real_api_settings), \
             patch("deeprca.graph.subgraphs.rpc_expert.httpx.AsyncClient", return_value=mock_client):
            agent = RPCExpertAgent()
            result = await agent.analyze({"service_name": "test-svc"}, {})

        finding_types = {f["type"] for f in result.findings}
        assert "call_volume_spike" in finding_types


# ─────────────────────────────────────────────
# 并发调度测试
# ─────────────────────────────────────────────

class TestDispatchToExperts:
    """PRD-03 §10: 6 维度同时执行 → 部分失败不影响整体。"""

    @pytest.mark.asyncio
    async def test_dispatch_all_four_experts(self):
        """downstream 维度触发 4 个领域专家并发。"""
        task_plan = [
            {"dimension": "downstream", "params": {}, "timeout": 5},
            {"dimension": "upstream", "params": {}, "timeout": 5},
        ]
        alert = {"service_name": "test-svc", "timestamp": "2026-07-10T14:00:00Z"}

        with patch("deeprca.graph.subgraphs.registry.get_expert_agent") as mock_get:
            mock_agent = MagicMock()
            mock_agent.analyze = AsyncMock(return_value=SubAgentResult(
                agent_name="mock_expert",
                dimension="mock",
                confidence=0.5,
                timestamp="2026-07-10T14:00:00Z",
            ))
            mock_get.return_value = mock_agent

            results = await dispatch_to_experts(task_plan, alert, timeout=5)

        # downstream → db, redis, mafka, rpc (4个) + upstream → rpc (已去重)
        # 总共 4 个唯一领域
        assert len(results) == 4
        for r in results:
            assert isinstance(r, SubAgentResult)
            assert r.confidence >= 0.0

    @pytest.mark.asyncio
    async def test_dispatch_partial_failure(self):
        """部分专家失败不影响其他专家。"""
        task_plan = [{"dimension": "downstream", "params": {}, "timeout": 5}]
        alert = {"service_name": "test-svc"}

        call_count = 0

        class FakeAgent:
            def __init__(self, name):
                self.agent_name = name

            async def analyze(self, alert, context):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise RuntimeError("模拟失败")
                return SubAgentResult(
                    agent_name=self.agent_name,
                    dimension=self.agent_name,
                    confidence=0.7,
                    timestamp="2026-07-10T14:00:00Z",
                )

        with patch("deeprca.graph.subgraphs.registry.get_expert_agent") as mock_get:
            mock_get.side_effect = lambda domain: FakeAgent(f"{domain}_expert")
            results = await dispatch_to_experts(task_plan, alert, timeout=5)

        assert len(results) == 4
        # 至少有 3 个成功（1 个失败）
        success_count = sum(1 for r in results if r.error is None)
        assert success_count >= 3
        # 失败的有错误信息
        failed = [r for r in results if r.error is not None]
        assert len(failed) == 1
        assert "执行失败" in failed[0].error

    @pytest.mark.asyncio
    async def test_dispatch_empty_plan(self):
        """空任务计划 → 返回空列表。"""
        results = await dispatch_to_experts([], {})
        assert results == []

    @pytest.mark.asyncio
    async def test_dispatch_no_trigger_dimensions(self):
        """change/errorlog/problem 维度不触发 L2 专家。"""
        task_plan = [
            {"dimension": "change", "params": {}, "timeout": 5},
            {"dimension": "errorlog", "params": {}, "timeout": 5},
            {"dimension": "problem", "params": {}, "timeout": 5},
        ]
        results = await dispatch_to_experts(task_plan, {})
        assert results == []

    @pytest.mark.asyncio
    async def test_dispatch_timeout(self):
        """单专家超时 → 降级为 error result。"""
        task_plan = [{"dimension": "upstream", "params": {}, "timeout": 5}]
        alert = {"service_name": "test-svc"}

        class SlowAgent:
            agent_name = "rpc_expert"

            async def analyze(self, alert, context):
                await asyncio.sleep(10)
                return SubAgentResult(
                    agent_name="rpc_expert",
                    dimension="rpc",
                    confidence=0.9,
                )

        with patch("deeprca.graph.subgraphs.registry.get_expert_agent") as mock_get:
            mock_get.return_value = SlowAgent()
            results = await dispatch_to_experts(task_plan, alert, timeout=1)

        assert len(results) == 1
        assert results[0].error is not None
        assert "超时" in results[0].error
