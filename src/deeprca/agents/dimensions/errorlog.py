"""错误日志维度分析器 — analyze_errorlog。

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
from deeprca.tools import query_error_logs


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


async def analyze_errorlog(alert: dict) -> SubAgentResult:
    """错误日志维度分析：查询 ERROR 级别日志，提取异常线索。

    Args:
        alert: 告警信息字典，包含 service_name, timestamp, labels 等字段

    Returns:
        SubAgentResult: 错误日志维度分析结果
    """
    service_name = alert.get("service_name", "")
    timestamp_str = alert.get("timestamp", "")
    start_time, end_time = _compute_time_window(timestamp_str)

    try:
        result = await query_error_logs.ainvoke({
            "service_name": service_name,
            "start_time": start_time,
            "end_time": end_time,
            "level": "ERROR",
            "limit": 100,
        })

        if result.get("error"):
            return SubAgentResult(
                agent_name="errorlog_analyzer",
                dimension="errorlog",
                confidence=0.0,
                error=result["error"],
                timestamp=timestamp_str,
            )

        logs = result.get("logs", [])
        findings: list[dict] = []
        evidence: list[str] = []

        for log_entry in logs:
            log_msg = log_entry.get("message", log_entry.get("content", ""))
            log_time = log_entry.get("timestamp", log_entry.get("time", ""))
            findings.append({
                "type": "error_log",
                "time": log_time,
                "desc": log_msg[:200] if isinstance(log_msg, str) else str(log_msg)[:200],
                "level": log_entry.get("level", "ERROR"),
            })

        if logs:
            evidence.append(f"查询到 {len(logs)} 条 ERROR 日志")
            # 提取前 3 条日志摘要作为证据
            for log_entry in logs[:3]:
                msg = log_entry.get("message", log_entry.get("content", ""))
                evidence.append(f"  - {str(msg)[:100]}")
        else:
            evidence.append("未发现 ERROR 级别日志")

        # confidence 计算：日志数量越多置信度越高
        if len(logs) == 0:
            confidence = 0.1
        elif len(logs) <= 5:
            confidence = 0.5
        elif len(logs) <= 20:
            confidence = 0.7
        else:
            confidence = 0.9

        return SubAgentResult(
            agent_name="errorlog_analyzer",
            dimension="errorlog",
            findings=findings,
            confidence=confidence,
            evidence=evidence,
            timestamp=timestamp_str,
        )
    except Exception as e:
        return SubAgentResult(
            agent_name="errorlog_analyzer",
            dimension="errorlog",
            confidence=0.0,
            error=str(e),
            timestamp=timestamp_str,
        )
