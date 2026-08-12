"""集群资源维度分析器 — analyze_cluster。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from deeprca.models import SubAgentResult
from deeprca.tools import query_metrics


def _compute_time_window(timestamp_str: str) -> tuple[str, str]:
    """根据告警时间戳构造 30 分钟时间窗口。

    Args:
        timestamp_str: 告警时间戳 ISO 8601

    Returns:
        (start_time, end_time) ISO 8601 字符串
    """
    try:
        end_dt = datetime.fromisoformat(timestamp_str)
    except Exception:
        end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(minutes=30)
    return start_dt.isoformat(), end_dt.isoformat()


# DB/Redis/Mafka 相关异常关键词
_INFRA_KEYWORDS = ["db", "database", "mysql", "redis", "mafka", "kafka", "connection", "timeout", "refused"]


async def analyze_cluster(alert: dict) -> SubAgentResult:
    """集群资源维度分析：查询 CPU/内存/网络指标，检测基础设施异常。

    Args:
        alert: 告警信息字典，包含 service_name, timestamp, labels 等字段

    Returns:
        SubAgentResult: 集群资源维度分析结果
    """
    service_name = alert.get("service_name", "")
    timestamp_str = alert.get("timestamp", "")
    labels = alert.get("labels", {})
    start_time, end_time = _compute_time_window(timestamp_str)

    try:
        # 并发查询 CPU、内存、网络指标
        cpu_result, mem_result, net_result = await asyncio.gather(
            query_metrics.ainvoke({
                "service_name": service_name,
                "metric_name": "cpu_usage",
                "start_time": start_time,
                "end_time": end_time,
                "labels": labels,
            }),
            query_metrics.ainvoke({
                "service_name": service_name,
                "metric_name": "memory_usage",
                "start_time": start_time,
                "end_time": end_time,
                "labels": labels,
            }),
            query_metrics.ainvoke({
                "service_name": service_name,
                "metric_name": "network_io",
                "start_time": start_time,
                "end_time": end_time,
                "labels": labels,
            }),
        )

        findings: list[dict] = []
        evidence: list[str] = []

        # 分析 CPU 使用率
        cpu_data = cpu_result.get("data_points", [])
        if cpu_result.get("error"):
            evidence.append(f"CPU 指标查询失败: {cpu_result['error']}")
        elif cpu_data:
            values = [p.get("value", 0) for p in cpu_data if isinstance(p.get("value"), (int, float))]
            if values and max(values) > 80:  # CPU 超过 80%
                findings.append({
                    "type": "cpu_high",
                    "desc": f"CPU 使用率过高: 峰值 {max(values):.1f}%",
                    "severity": "high",
                    "metric": "cpu_usage",
                })
                evidence.append(f"CPU 峰值 {max(values):.1f}%")

        # 分析内存使用率
        mem_data = mem_result.get("data_points", [])
        if mem_result.get("error"):
            evidence.append(f"内存指标查询失败: {mem_result['error']}")
        elif mem_data:
            values = [p.get("value", 0) for p in mem_data if isinstance(p.get("value"), (int, float))]
            if values and max(values) > 85:  # 内存超过 85%
                findings.append({
                    "type": "memory_high",
                    "desc": f"内存使用率过高: 峰值 {max(values):.1f}%",
                    "severity": "high",
                    "metric": "memory_usage",
                })
                evidence.append(f"内存峰值 {max(values):.1f}%")

        # 分析网络 IO
        net_data = net_result.get("data_points", [])
        if net_result.get("error"):
            evidence.append(f"网络指标查询失败: {net_result['error']}")
        elif net_data:
            values = [p.get("value", 0) for p in net_data if isinstance(p.get("value"), (int, float))]
            if values:
                avg_net = sum(values) / len(values) if values else 0
                if avg_net > 0:
                    findings.append({
                        "type": "network_io",
                        "desc": f"网络 IO 平均值: {avg_net:.2f}",
                        "severity": "low",
                        "metric": "network_io",
                    })

        # 检查告警描述中是否包含 DB/Redis/Mafka 关键词
        alert_desc = alert.get("description", "").lower()
        for keyword in _INFRA_KEYWORDS:
            if keyword in alert_desc:
                findings.append({
                    "type": "infra_keyword",
                    "keyword": keyword,
                    "desc": f"告警描述中包含基础设施关键词: {keyword}",
                    "severity": "medium",
                    "note": "供 L2 领域专家触发判断使用",
                })
                evidence.append(f"检测到基础设施关键词: {keyword}")
                break  # 只标注一次

        # confidence 计算
        high_count = sum(1 for f in findings if f.get("severity") == "high")
        medium_count = sum(1 for f in findings if f.get("severity") == "medium")
        confidence = min(0.9, 0.2 + 0.3 * high_count + 0.15 * medium_count) if findings else 0.1

        return SubAgentResult(
            agent_name="cluster_analyzer",
            dimension="cluster",
            findings=findings,
            confidence=confidence,
            evidence=evidence,
            timestamp=timestamp_str,
        )
    except Exception as e:
        return SubAgentResult(
            agent_name="cluster_analyzer",
            dimension="cluster",
            confidence=0.0,
            error=str(e),
            timestamp=timestamp_str,
        )
