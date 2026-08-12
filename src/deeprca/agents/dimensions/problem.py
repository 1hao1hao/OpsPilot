"""关联告警维度分析器 — analyze_problem。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

from deeprca.models import SubAgentResult
from deeprca.tools import query_related_alerts


async def analyze_problem(alert: dict) -> SubAgentResult:
    """关联告警维度分析：查询关联告警，判断是否存在级联故障。

    Args:
        alert: 告警信息字典，包含 service_name, timestamp, labels 等字段

    Returns:
        SubAgentResult: 关联告警维度分析结果
    """
    service_name = alert.get("service_name", "")
    timestamp_str = alert.get("timestamp", "")

    try:
        result = await query_related_alerts.ainvoke({
            "service_name": service_name,
            "time_range": "30m",
        })

        if result.get("error"):
            return SubAgentResult(
                agent_name="problem_analyzer",
                dimension="problem",
                confidence=0.0,
                error=result["error"],
                timestamp=timestamp_str,
            )

        related_alerts = result.get("related_alerts", [])
        findings: list[dict] = []
        evidence: list[str] = []

        for ra in related_alerts:
            findings.append({
                "type": "related_alert",
                "alert_id": ra.get("alert_id", ""),
                "service": ra.get("service_name", ""),
                "desc": ra.get("description", ra.get("desc", "")),
                "severity": ra.get("severity", ""),
                "time": ra.get("timestamp", ra.get("time", "")),
            })

        if related_alerts:
            evidence.append(f"查询到 {len(related_alerts)} 条关联告警")
            # 检查是否有其他服务的告警（级联故障迹象）
            other_services = {ra.get("service_name", "") for ra in related_alerts} - {service_name}
            if other_services:
                evidence.append(f"发现其他服务告警（可能级联）: {', '.join(other_services)}")
        else:
            evidence.append("未发现关联告警")

        # confidence 计算：关联告警越多，级联故障可能性越高
        if len(related_alerts) == 0:
            confidence = 0.1
        elif len(related_alerts) == 1:
            confidence = 0.4
        elif len(related_alerts) <= 3:
            confidence = 0.7
        else:
            confidence = 0.9

        return SubAgentResult(
            agent_name="problem_analyzer",
            dimension="problem",
            findings=findings,
            confidence=confidence,
            evidence=evidence,
            timestamp=timestamp_str,
        )
    except Exception as e:
        return SubAgentResult(
            agent_name="problem_analyzer",
            dimension="problem",
            confidence=0.0,
            error=str(e),
            timestamp=timestamp_str,
        )
