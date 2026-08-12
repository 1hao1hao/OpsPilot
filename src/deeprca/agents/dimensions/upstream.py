"""上游维度分析器 — analyze_upstream。

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
from deeprca.tools import query_metrics, query_topology


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


async def analyze_upstream(alert: dict) -> SubAgentResult:
    """上游维度分析：查询上游 QPS 和错误率，分析拓扑依赖。

    Args:
        alert: 告警信息字典，包含 service_name, timestamp, labels 等字段

    Returns:
        SubAgentResult: 上游维度分析结果
    """
    service_name = alert.get("service_name", "")
    timestamp_str = alert.get("timestamp", "")
    labels = alert.get("labels", {})
    start_time, end_time = _compute_time_window(timestamp_str)

    try:
        # 并发查询上游指标和拓扑
        qps_result, error_rate_result, topo_result = await asyncio.gather(
            query_metrics.ainvoke({
                "service_name": service_name,
                "metric_name": "upstream_qps",
                "start_time": start_time,
                "end_time": end_time,
                "labels": labels,
            }),
            query_metrics.ainvoke({
                "service_name": service_name,
                "metric_name": "upstream_error_rate",
                "start_time": start_time,
                "end_time": end_time,
                "labels": labels,
            }),
            query_topology.ainvoke({
                "service_name": service_name,
                "depth": 2,
            }),
        )

        findings: list[dict] = []
        evidence: list[str] = []

        # 分析上游 QPS 异常
        qps_data = qps_result.get("data_points", [])
        if qps_result.get("error"):
            evidence.append(f"上游 QPS 查询失败: {qps_result['error']}")
        elif qps_data:
            # 简单检测 QPS 突降（最后值低于平均值的 50%）
            values = [p.get("value", 0) for p in qps_data if isinstance(p.get("value"), (int, float))]
            if values:
                avg_qps = sum(values) / len(values)
                last_qps = values[-1]
                if avg_qps > 0 and last_qps < avg_qps * 0.5:
                    findings.append({
                        "type": "upstream_qps_drop",
                        "desc": f"上游 QPS 突降: 当前 {last_qps:.2f}, 平均 {avg_qps:.2f}",
                        "severity": "high",
                    })
                    evidence.append("检测到上游 QPS 突降")

        # 分析上游错误率
        error_data = error_rate_result.get("data_points", [])
        if error_rate_result.get("error"):
            evidence.append(f"上游错误率查询失败: {error_rate_result['error']}")
        elif error_data:
            values = [p.get("value", 0) for p in error_data if isinstance(p.get("value"), (int, float))]
            if values and max(values) > 0.05:  # 错误率超过 5%
                findings.append({
                    "type": "upstream_error_rate",
                    "desc": f"上游错误率异常: 峰值 {max(values):.2%}",
                    "severity": "high",
                })
                evidence.append("检测到上游错误率异常")

        # 分析上游依赖拓扑
        upstream_deps = topo_result.get("upstream", [])
        if topo_result.get("error"):
            evidence.append(f"拓扑查询失败: {topo_result['error']}")
        elif upstream_deps:
            for dep in upstream_deps:
                findings.append({
                    "type": "upstream_dependency",
                    "service": dep.get("service", dep.get("name", "")),
                    "desc": f"上游依赖: {dep.get('service', dep.get('name', ''))}",
                })
            evidence.append(f"发现 {len(upstream_deps)} 个上游依赖")

        # confidence 计算
        anomaly_count = sum(1 for f in findings if f.get("severity") in ("high", "medium"))
        confidence = min(0.9, 0.3 + 0.2 * anomaly_count) if findings else 0.1

        return SubAgentResult(
            agent_name="upstream_analyzer",
            dimension="upstream",
            findings=findings,
            confidence=confidence,
            evidence=evidence,
            timestamp=timestamp_str,
        )
    except Exception as e:
        return SubAgentResult(
            agent_name="upstream_analyzer",
            dimension="upstream",
            confidence=0.0,
            error=str(e),
            timestamp=timestamp_str,
        )
