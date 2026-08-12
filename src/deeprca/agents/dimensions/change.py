"""变更维度分析器 — analyze_change。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from deeprca.models import SubAgentResult
from deeprca.tools import query_recent_changes


async def analyze_change(alert: dict) -> SubAgentResult:
    """变更维度分析：查询近期变更记录，判断是否与告警相关。

    Args:
        alert: 告警信息字典，包含 service_name, timestamp, labels 等字段

    Returns:
        SubAgentResult: 变更维度分析结果
    """
    service_name = alert.get("service_name", "")
    timestamp_str = alert.get("timestamp", "")

    try:
        result = await query_recent_changes.ainvoke({
            "service_name": service_name,
            "time_range": "30m",
        })

        if result.get("error"):
            return SubAgentResult(
                agent_name="change_analyzer",
                dimension="change",
                confidence=0.0,
                error=result["error"],
                timestamp=timestamp_str,
            )

        changes = result.get("changes", [])
        findings: list[dict] = []
        for ch in changes:
            findings.append({
                "type": ch.get("type", "deployment"),
                "time": ch.get("timestamp", ch.get("time", "")),
                "desc": ch.get("description", ch.get("desc", "")),
                "operator": ch.get("operator", ""),
            })

        # confidence 根据变更数量计算
        if len(changes) == 0:
            confidence = 0.1
        elif len(changes) == 1:
            confidence = 0.7
        else:
            confidence = min(0.9, 0.5 + 0.2 * len(changes))

        return SubAgentResult(
            agent_name="change_analyzer",
            dimension="change",
            findings=findings,
            confidence=confidence,
            evidence=[f"查询到 {len(changes)} 条近期变更记录"] if changes else ["未发现近期变更"],
            timestamp=timestamp_str,
        )
    except Exception as e:
        return SubAgentResult(
            agent_name="change_analyzer",
            dimension="change",
            confidence=0.0,
            error=str(e),
            timestamp=timestamp_str,
        )
