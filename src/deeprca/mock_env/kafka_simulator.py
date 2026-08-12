"""Kafka/Mafka 消息队列模拟器 — 模拟消费者离线、Rebalance 风暴、消费速率下降等故障。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：Kafka 模拟器</td><td>PRD-05 §4.3</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class KafkaSimulator:
    """Kafka 消息队列模拟器。

    模拟消费者离线、Rebalance 风暴、消费速率下降等故障场景，
    支持消费积压查询和故障注入。
    """

    def __init__(self, cluster: str = "kafka-prod-01") -> None:
        self.cluster = cluster
        self.topics: dict[str, dict[str, Any]] = {
            "order-events": {
                "partitions": 3,
                "consumer_groups": {
                    "order-consumer-group": {
                        "consumers": ["consumer-1", "consumer-2", "consumer-3"],
                        "lag_per_partition": [100, 100, 100],
                        "rebalance_count": 0,
                    }
                },
            }
        }
        self.produce_rate: int = 1000  # msg/s
        self.consume_rate: int = 1000  # msg/s

    # ─────────────────────────────────────
    # 故障注入
    # ─────────────────────────────────────
    def inject_consumer_offline(self, topic: str = "order-events", group: str = "order-consumer-group", consumer: str = "consumer-3") -> dict[str, Any]:
        """注入消费者离线。"""
        topic_data = self.topics.get(topic, {})
        group_data = topic_data.get("consumer_groups", {}).get(group, {})
        if consumer in group_data.get("consumers", []):
            group_data["consumers"].remove(consumer)
            offline_partitions = len(group_data["lag_per_partition"]) - len(group_data["consumers"])
            for i in range(offline_partitions):
                group_data["lag_per_partition"][i] += 10000
        return {
            "status": "injected",
            "topic": topic,
            "group": group,
            "remaining_consumers": len(group_data.get("consumers", [])),
        }

    def inject_rebalance_storm(self, topic: str = "order-events", group: str = "order-consumer-group", count: int = 5) -> dict[str, Any]:
        """注入 Rebalance 风暴。"""
        topic_data = self.topics.get(topic)
        if not topic_data:
            return {"error": "topic not found"}
        group_data = topic_data["consumer_groups"].get(group, {})
        group_data["rebalance_count"] = count
        return {"status": "injected", "topic": topic, "group": group, "rebalance_count": count}

    def inject_consume_rate_drop(self, rate: int = 300) -> dict[str, Any]:
        """注入消费速率下降。"""
        self.consume_rate = rate
        return {"status": "injected", "consume_rate": rate, "produce_rate": self.produce_rate}

    # ─────────────────────────────────────
    # 查询
    # ─────────────────────────────────────
    def get_consumer_lag(self, topic: str = "order-events", group: str = "order-consumer-group") -> dict[str, Any]:
        """获取消费者积压详情。"""
        topic_data = self.topics.get(topic, {})
        group_data = topic_data.get("consumer_groups", {}).get(group, {})
        lag_list = group_data.get("lag_per_partition", [])
        consumers = group_data.get("consumers", [])
        return {
            "cluster": self.cluster,
            "topic": topic,
            "consumer_group": group,
            "total_lag": sum(lag_list),
            "partitions": [
                {
                    "partition": i,
                    "lag": lag,
                    "consumer": consumers[i] if i < len(consumers) else None,
                }
                for i, lag in enumerate(lag_list)
            ],
            "rebalance_events": group_data.get("rebalance_count", 0),
            "produce_rate": self.produce_rate,
            "consume_rate": self.consume_rate,
        }

    def get_metrics(self) -> dict[str, Any]:
        """获取 Kafka 指标。"""
        total_lag = 0
        for topic_data in self.topics.values():
            for group_data in topic_data.get("consumer_groups", {}).values():
                total_lag += sum(group_data.get("lag_per_partition", []))
        return {
            "cluster": self.cluster,
            "topics": list(self.topics.keys()),
            "produce_rate": self.produce_rate,
            "consume_rate": self.consume_rate,
            "total_lag": total_lag,
        }

    def reset(self) -> None:
        """重置到基线状态。"""
        self.topics = {
            "order-events": {
                "partitions": 3,
                "consumer_groups": {
                    "order-consumer-group": {
                        "consumers": ["consumer-1", "consumer-2", "consumer-3"],
                        "lag_per_partition": [100, 100, 100],
                        "rebalance_count": 0,
                    }
                },
            }
        }
        self.produce_rate = 1000
        self.consume_rate = 1000
