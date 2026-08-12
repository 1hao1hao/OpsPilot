"""Redis 缓存模拟器 — 模拟内存压力、命中率下降、热点 Key、大 Key 等故障。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：Redis 模拟器</td><td>PRD-05 §4.2</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class RedisSimulator:
    """Redis 缓存模拟器。

    模拟内存压力、命中率下降、热点 Key、大 Key 等故障场景，
    支持指标查询和故障注入。
    """

    def __init__(self, instance_name: str = "redis-cluster-01") -> None:
        self.instance = instance_name
        self.used_memory: float = 4.0  # GB
        self.maxmemory: float = 8.0    # GB
        self.hit_rate: float = 0.95
        self.connected_clients: int = 50
        self.maxclients: int = 200
        self.ops_per_sec: int = 5000
        self.evicted_keys: int = 0
        self.hotkeys: list[dict[str, Any]] = []
        self.bigkeys: list[dict[str, Any]] = []
        self.slowlog: list[dict[str, Any]] = []

    # ─────────────────────────────────────
    # 故障注入
    # ─────────────────────────────────────
    def inject_memory_pressure(self, used_memory: float = 7.5) -> dict[str, Any]:
        """注入内存压力。"""
        self.used_memory = used_memory
        if used_memory / self.maxmemory > 0.85:
            import random
            self.evicted_keys = random.randint(100, 1000)
        return {
            "status": "injected",
            "used_memory_gb": used_memory,
            "usage_ratio": round(used_memory / self.maxmemory, 4),
            "evicted_keys": self.evicted_keys,
        }

    def inject_hit_rate_drop(self, hit_rate: float = 0.70) -> dict[str, Any]:
        """注入命中率下降。"""
        self.hit_rate = hit_rate
        return {
            "status": "injected",
            "hit_rate": hit_rate,
            "drop_pp": round((0.95 - hit_rate) * 100, 2),
        }

    def inject_hotkey(self, key: str = "user:session:hot", qps: int = 15000) -> dict[str, Any]:
        """注入热点 Key。"""
        entry = {"key": key, "qps": qps, "type": "string", "size": "1KB", "timestamp": _now_iso()}
        self.hotkeys.append(entry)
        return {"status": "injected", "hotkey": entry}

    def inject_bigkey(self, key: str = "order:cache:batch", size_mb: float = 15) -> dict[str, Any]:
        """注入大 Key。"""
        entry = {"key": key, "size_mb": size_mb, "type": "hash", "timestamp": _now_iso()}
        self.bigkeys.append(entry)
        return {"status": "injected", "bigkey": entry}

    # ─────────────────────────────────────
    # 查询
    # ─────────────────────────────────────
    def get_metrics(self, metric_names: list[str] | None = None) -> dict[str, Any]:
        """获取指标。"""
        metric_names = metric_names or [
            "used_memory", "hit_rate", "connected_clients", "ops_per_sec", "evicted_keys",
        ]
        result: dict[str, Any] = {}
        for metric in metric_names:
            if metric == "used_memory":
                result[metric] = {
                    "current_gb": self.used_memory,
                    "max_gb": self.maxmemory,
                    "usage_ratio": round(self.used_memory / self.maxmemory, 4),
                }
            elif metric == "hit_rate":
                result[metric] = {
                    "current": self.hit_rate,
                    "baseline": 0.95,
                    "drop_pp": round((0.95 - self.hit_rate) * 100, 2),
                }
            elif metric == "connected_clients":
                result[metric] = {
                    "current": self.connected_clients,
                    "max": self.maxclients,
                    "usage_ratio": round(self.connected_clients / self.maxclients, 4),
                }
            elif metric == "ops_per_sec":
                result[metric] = {"current": self.ops_per_sec}
            elif metric == "evicted_keys":
                result[metric] = {"current": self.evicted_keys}
        return result

    def get_hotkeys(self) -> list[dict[str, Any]]:
        """获取热点 Key 列表。"""
        return list(self.hotkeys)

    def get_topology(self) -> dict[str, Any]:
        """获取 Redis 拓扑。"""
        return {
            "instance": self.instance,
            "type": "cluster",
            "nodes": 6,
            "maxmemory_gb": self.maxmemory,
            "used_memory_gb": self.used_memory,
        }

    def reset(self) -> None:
        """重置到基线状态。"""
        self.used_memory = 4.0
        self.hit_rate = 0.95
        self.connected_clients = 50
        self.ops_per_sec = 5000
        self.evicted_keys = 0
        self.hotkeys.clear()
        self.bigkeys.clear()
        self.slowlog.clear()
