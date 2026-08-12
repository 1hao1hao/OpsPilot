"""L2 领域专家在 Mock 模式下的本地数据源。

当 settings.mock_env_enabled=True 时，DB/Redis/Mafka/RPC 四个专家不再请求
外部 /api/metrics/{domain} 端点（该端点在 mock-env 中不存在），
而是直接返回与子 Agent 阈值分析器口径一致的扁平指标字典。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：L2 专家 Mock 数据源</td><td>PRD-05 Mock 环境, reviewer-fix</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

import random


def mock_db_metrics(service: str = "") -> dict:
    """DB 专家期望的扁平指标格式。"""
    random.seed(hash(service + "db") & 0xFFFF)
    return {
        "slow_query_count": random.randint(0, 10),
        "active_connections": random.randint(50, 120),
        "max_connections": 200,
        "lock_wait_count": random.randint(0, 2),
        "replication_lag_seconds": round(random.uniform(0.1, 2.0), 1),
    }


def mock_redis_metrics(service: str = "") -> dict:
    """Redis 专家期望的扁平指标格式。"""
    random.seed(hash(service + "redis") & 0xFFFF)
    return {
        "memory_usage_percent": round(random.uniform(50.0, 75.0), 1),
        "hit_rate_percent": round(random.uniform(90.0, 98.0), 1),
        "connected_clients": random.randint(50, 120),
        "max_clients": 200,
        "big_keys_count": random.randint(0, 1),
    }


def mock_kafka_metrics(service: str = "") -> dict:
    """Mafka 专家期望的扁平指标格式。"""
    random.seed(hash(service + "kafka") & 0xFFFF)
    return {
        "consumer_lag": random.randint(100, 2000),
        "produce_rate": random.randint(800, 1200),
        "consume_rate": random.randint(800, 1200),
        "rebalance_count": 0,
    }


def mock_rpc_metrics(service: str = "") -> dict:
    """RPC 专家期望的扁平指标格式。"""
    random.seed(hash(service + "rpc") & 0xFFFF)
    return {
        "failure_rate_percent": round(random.uniform(0.1, 2.0), 1),
        "avg_rt_ms": random.randint(40, 80),
        "baseline_rt_ms": 50,
        "timeout_count": 0,
        "call_volume": random.randint(800, 1200),
        "baseline_volume": 1000,
    }
