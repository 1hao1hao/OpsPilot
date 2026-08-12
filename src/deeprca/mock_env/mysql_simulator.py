"""MySQL 数据库模拟器 — 模拟主从延迟、连接池、慢查询、锁等待等故障。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：MySQL 模拟器</td><td>PRD-05 §4.1</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class MySQLSimulator:
    """MySQL 数据库模拟器。

    模拟主从延迟、连接池耗尽、慢查询、锁等待等故障场景，
    支持指标查询和故障注入。
    """

    def __init__(self, instance_name: str = "mysql-prod-01") -> None:
        self.instance = instance_name
        self.slaves: list[dict[str, Any]] = [
            {"instance": f"{instance_name}-slave-01", "delay_seconds": 0.5, "status": "normal"},
            {"instance": f"{instance_name}-slave-02", "delay_seconds": 0.3, "status": "normal"},
        ]
        self.connection_pool: dict[str, int] = {"max": 200, "active": 80, "idle": 120, "waiting": 0}
        self.slow_queries: list[dict[str, Any]] = []
        self.lock_waits: list[dict[str, Any]] = []
        self.io_wait: float = 5.0

    # ─────────────────────────────────────
    # 故障注入
    # ─────────────────────────────────────
    def inject_slave_delay(self, delay_seconds: float, slave_index: int = 0) -> dict[str, Any]:
        """注入主从延迟。"""
        if slave_index < len(self.slaves):
            self.slaves[slave_index]["delay_seconds"] = delay_seconds
            self.slaves[slave_index]["status"] = "lagging" if delay_seconds > 5 else "normal"
        return {"status": "injected", "slave": self.slaves[slave_index]["instance"], "delay_seconds": delay_seconds}

    def inject_connection_pool_exhaustion(self, active: int = 180) -> dict[str, Any]:
        """注入连接池耗尽。"""
        self.connection_pool["active"] = active
        self.connection_pool["idle"] = max(0, self.connection_pool["max"] - active)
        self.connection_pool["waiting"] = max(0, active - self.connection_pool["max"] + 20)
        return {"status": "injected", "active": active, "waiting": self.connection_pool["waiting"]}

    def inject_slow_query(self, sql: str = "SELECT * FROM orders", duration_ms: int = 800, count: int = 1) -> dict[str, Any]:
        """注入慢查询。"""
        for _ in range(count):
            self.slow_queries.append({
                "sql": sql,
                "duration_ms": duration_ms,
                "timestamp": _now_iso(),
                "rows_examined": 1500000,
                "rows_returned": 100,
                "index_used": "idx_created_at",
            })
        return {"status": "injected", "count": count, "total_slow_queries": len(self.slow_queries)}

    def inject_lock_wait(self, count: int = 5) -> dict[str, Any]:
        """注入锁等待。"""
        for _ in range(count):
            self.lock_waits.append({
                "transaction_id": f"tx-{random.randint(10000, 99999)}",
                "lock_type": "RECORD",
                "table": "orders",
                "waiting_seconds": round(random.uniform(5, 30), 2),
                "timestamp": _now_iso(),
            })
        return {"status": "injected", "count": count, "total_lock_waits": len(self.lock_waits)}

    # ─────────────────────────────────────
    # 查询
    # ─────────────────────────────────────
    def get_metrics(self, metric_names: list[str] | None = None) -> dict[str, Any]:
        """获取指标数据。"""
        metric_names = metric_names or [
            "active_connections", "slow_query_count", "slave_delay_seconds",
            "innodb_lock_waits", "io_wait_ratio",
        ]
        result: dict[str, Any] = {}
        for metric in metric_names:
            if metric == "active_connections":
                result[metric] = {
                    "current": self.connection_pool["active"],
                    "max": self.connection_pool["max"],
                    "usage_ratio": round(self.connection_pool["active"] / self.connection_pool["max"], 4),
                }
            elif metric == "slow_query_count":
                result[metric] = {
                    "current": len(self.slow_queries),
                    "baseline_avg": 5,
                    "anomaly_ratio": max(1.0, len(self.slow_queries) / 5) if self.slow_queries else 1.0,
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

    def get_slow_log(self) -> list[dict[str, Any]]:
        """获取慢日志。"""
        return list(self.slow_queries)

    def get_topology(self) -> dict[str, Any]:
        """获取 DB 拓扑。"""
        return {
            "instance": self.instance,
            "role": "master",
            "slaves": self.slaves,
            "connection_pool": self.connection_pool,
        }

    def reset(self) -> None:
        """重置到基线状态。"""
        self.slaves = [
            {"instance": f"{self.instance}-slave-01", "delay_seconds": 0.5, "status": "normal"},
            {"instance": f"{self.instance}-slave-02", "delay_seconds": 0.3, "status": "normal"},
        ]
        self.connection_pool = {"max": 200, "active": 80, "idle": 120, "waiting": 0}
        self.slow_queries.clear()
        self.lock_waits.clear()
        self.io_wait = 5.0
