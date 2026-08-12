"""Mock 数据生成器 — 为 6 个工具提供确定性模拟数据。

[已废弃] 工具层自 reviewer-fix-3 起已改为 HTTP 调用 Mock API（端口 8001）获取场景感知数据，
此文件不再被任何模块导入使用，保留仅供历史参考。
如需恢复内联 Mock 模式，请移除此注释并重新接线工具层导入。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：6 个工具的 mock 数据生成</td><td>PRD-02 §3.3, PRD-05 Mock 环境</td>
</tr></table>
@author DeepRCA Team
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def mock_metrics(
    service_name: str,
    metric_name: str,
    start_time: str,
    end_time: str,
    granularity: str = "1m",
    labels: dict | None = None,
) -> dict:
    """生成模拟监控指标时序数据。"""
    # 根据 metric_name 生成不同特征的数据
    random.seed(hash(service_name + metric_name) & 0xFFFF)
    points: list[dict] = []

    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        start_dt = datetime.now(timezone.utc) - timedelta(minutes=30)
        end_dt = datetime.now(timezone.utc)

    delta = (end_dt - start_dt).total_seconds()
    step = 60 if granularity == "1m" else (300 if granularity == "5m" else 3600)
    num_points = max(int(delta / step), 1)

    base_values = {
        "qps": 1000, "error_rate": 0.01, "tp99": 50, "tp95": 30,
        "cpu_usage": 40, "memory_usage": 60, "disk_usage": 50,
    }
    base = base_values.get(metric_name, 50)

    # 在中段注入一个异常突刺
    spike_idx = num_points // 2
    for i in range(num_points):
        ts = (start_dt + timedelta(seconds=i * step)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        if i == spike_idx:
            value = base * 3  # 突刺
        elif i > spike_idx and i < spike_idx + 3:
            value = base * 2  # 突刺后余波
        else:
            value = base * (1 + random.uniform(-0.1, 0.1))
        points.append({"timestamp": ts, "value": round(value, 2)})

    values = [p["value"] for p in points]
    return {
        "service": service_name,
        "metric": metric_name,
        "data_points": points,
        "aggregation": {
            "min": min(values) if values else 0,
            "max": max(values) if values else 0,
            "avg": sum(values) / len(values) if values else 0,
        },
    }


def mock_error_logs(
    service_name: str,
    start_time: str,
    end_time: str,
    level: str = "ERROR",
    keyword: str = "",
    limit: int = 100,
) -> dict:
    """生成模拟错误日志。"""
    random.seed(hash(service_name + "logs") & 0xFFFF)
    error_messages = [
        "Connection refused: downstream service timeout",
        "Lock wait timeout exceeded; try restarting transaction",
        "OutOfMemoryError: Java heap space",
        "Circuit breaker opened: downstream failure rate exceeded threshold",
        "Redis connection pool exhausted",
        "Kafka consumer lag detected: 50000 messages behind",
    ]

    logs: list[dict] = []
    for i in range(min(limit, 20)):
        ts = _now_iso()
        logs.append({
            "timestamp": ts,
            "level": level,
            "message": random.choice(error_messages),
            "service": service_name,
            "host": f"{service_name}-pod-{i:03d}",
        })

    from collections import Counter
    import re as _re
    patterns = Counter()
    for log in logs:
        for match in _re.findall(r"([A-Z][a-z]+(?:\s+[a-z]+){1,3})", log["message"]):
            patterns[match] += 1
    error_patterns = [
        {"pattern": p, "count": c, "first_seen": logs[0]["timestamp"] if logs else ""}
        for p, c in patterns.most_common(5)
    ]
    return {
        "service": service_name,
        "total": len(logs),
        "logs": logs,
        "error_patterns": error_patterns,
    }


def mock_recent_changes(
    service_name: str,
    time_range: str = "24h",
    change_type: str = "",
) -> dict:
    """生成模拟变更记录。"""
    random.seed(hash(service_name + "changes") & 0xFFFF)
    change_types = ["deploy", "config", "scale", "rollback"]
    changes: list[dict] = []
    # 生成 1-3 条变更记录
    num_changes = random.randint(1, 3)
    for i in range(num_changes):
        ct = random.choice(change_types) if not change_type else change_type
        changes.append({
            "change_id": f"chg-{service_name}-{i:03d}",
            "type": ct,
            "operator": random.choice(["zhangsan", "lisi", "wangwu"]),
            "timestamp": _now_iso(),
            "description": f"{ct} for {service_name}: version v{random.randint(2, 5)}.{random.randint(0, 9)}.{random.randint(0, 9)}",
            "status": "success",
        })
    return {
        "service": service_name,
        "total": len(changes),
        "changes": changes,
    }


def mock_topology(
    service_name: str,
    depth: int = 2,
    direction: str = "both",
) -> dict:
    """生成模拟服务拓扑。"""
    upstream = [
        {"service": "api-gateway", "type": "http", "qps": 500},
        {"service": "web-frontend", "type": "http", "qps": 200},
    ] if direction in ("upstream", "both") else []
    downstream = [
        {"service": "mysql-master", "type": "db", "role": "master"},
        {"service": "mysql-slave-01", "type": "db", "role": "slave"},
        {"service": "redis-cluster", "type": "cache"},
        {"service": "kafka-broker", "type": "mq"},
        {"service": "downstream-rpc-service", "type": "rpc"},
    ] if direction in ("downstream", "both") else []
    return {
        "service": service_name,
        "upstream": upstream,
        "downstream": downstream,
    }


def mock_trace(
    service_name: str,
    start_time: str,
    end_time: str,
    trace_id: str = "",
    status: str = "",
    limit: int = 50,
) -> dict:
    """生成模拟调用链路数据。"""
    random.seed(hash(service_name + "trace") & 0xFFFF)
    traces: list[dict] = []
    for i in range(min(limit, 10)):
        spans: list[dict] = []
        # 主 span
        spans.append({
            "service": service_name,
            "operation": "handle_request",
            "duration_ms": random.randint(50, 200),
            "status": "error" if random.random() < 0.3 else "success",
        })
        # 下游 span
        spans.append({
            "service": "mysql-slave-01",
            "operation": "SELECT",
            "duration_ms": random.randint(100, 500),
            "status": "timeout" if random.random() < 0.4 else "success",
        })
        traces.append({
            "trace_id": trace_id or f"trace-{i:04d}",
            "spans": spans,
            "start_time": _now_iso(),
            "duration_ms": sum(s["duration_ms"] for s in spans),
        })

    span_durations: dict[str, list[float]] = {}
    for trace in traces:
        for span in trace["spans"]:
            svc = span["service"]
            span_durations.setdefault(svc, []).append(span["duration_ms"])
    slow_spans = [
        {
            "service": svc,
            "avg_duration_ms": sum(d) / len(d),
            "p99_duration_ms": sorted(d)[int(len(d) * 0.99)] if len(d) > 1 else d[0],
        }
        for svc, d in span_durations.items()
    ]
    slow_spans.sort(key=lambda s: s["avg_duration_ms"], reverse=True)
    return {
        "service": service_name,
        "total": len(traces),
        "traces": traces,
        "slow_spans": slow_spans[:10],
    }


def mock_related_alerts(
    service_name: str,
    time_range: str = "7d",
    alert_type: str = "",
) -> dict:
    """生成模拟关联告警和已知问题。"""
    random.seed(hash(service_name + "alerts") & 0xFFFF)
    alerts: list[dict] = []
    for i in range(random.randint(2, 5)):
        alerts.append({
            "alert_id": f"alt-history-{i:03d}",
            "service_name": service_name,
            "alert_type": random.choice(["timeout", "error_rate", "resource"]),
            "severity": random.choice(["P1", "P2", "P3"]),
            "timestamp": _now_iso(),
            "description": f"Historical alert for {service_name}",
        })
    known_issues: list[dict] = [
        {
            "issue_id": "known-001",
            "title": f"{service_name} 已知性能问题",
            "description": "高峰期 tp99 偶发飙升至 500ms+",
            "solution": "扩容下游 DB 从库",
            "last_occurrence": _now_iso(),
        }
    ]
    return {
        "service": service_name,
        "related_alerts": alerts,
        "known_issues": known_issues,
    }
