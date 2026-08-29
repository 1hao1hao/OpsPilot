"""下游维度分析器 — analyze_downstream。

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
from deeprca.tools import query_trace, query_topology
from opspilot.tracing import detect_span_anomalies


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


async def analyze_downstream(alert: dict) -> SubAgentResult:
    """下游维度分析：查询调用链和下游依赖拓扑。

    Args:
        alert: 告警信息字典，包含 service_name, timestamp, labels 等字段

    Returns:
        SubAgentResult: 下游维度分析结果
    """
    service_name = alert.get("service_name", "")
    timestamp_str = alert.get("timestamp", "")
    start_time, end_time = _compute_time_window(timestamp_str)

    try:
        # 并发查询调用链和拓扑
        trace_result, topo_result = await asyncio.gather(
            query_trace.ainvoke({
                "service_name": service_name,
                "start_time": start_time,
                "end_time": end_time,
                "limit": 50,
            }),
            query_topology.ainvoke({
                "service_name": service_name,
                "depth": 2,
            }),
        )

        findings: list[dict] = []
        evidence: list[str] = []

        # 分析调用链异常
        traces = trace_result.get("traces", [])
        if trace_result.get("error"):
            evidence.append(f"调用链查询失败: {trace_result['error']}")
        elif traces:
            # 统一格式由 Trace Adapter 负责；分析器只处理 Span Tree，逐个子 Span
            # 检测异常，并保留根服务到异常服务的完整路径。
            for anomaly in detect_span_anomalies(trace_result):
                path = "/".join(anomaly.path)
                common = {
                    "service": anomaly.service,
                    "path": path,
                    "trace_id": anomaly.trace_id,
                    "span_id": anomaly.span_id,
                    "operation": anomaly.operation,
                    "duration_ms": anomaly.duration_ms,
                    "status": anomaly.status,
                    "severity": "high",
                }
                if anomaly.is_slow:
                    findings.append({
                        **common,
                        "type": "slow_downstream_call",
                        "desc": f"下游调用慢: {path} 耗时 {anomaly.duration_ms}ms",
                    })
                    evidence.append(f"下游路径 {path} 耗时 {anomaly.duration_ms}ms")

                if anomaly.is_error:
                    findings.append({
                        **common,
                        "type": "downstream_call_error",
                        "desc": f"下游调用错误: {path} 状态 {anomaly.status}",
                    })
                    evidence.append(f"下游路径 {path} 状态异常: {anomaly.status}")

            if traces:
                evidence.append(f"查询到 {len(traces)} 条调用链记录")

        # 分析下游依赖拓扑
        downstream_deps = topo_result.get("downstream", [])
        if topo_result.get("error"):
            evidence.append(f"拓扑查询失败: {topo_result['error']}")
        elif downstream_deps:
            for dep in downstream_deps:
                findings.append({
                    "type": "downstream_dependency",
                    "service": dep.get("service", dep.get("name", "")),
                    "desc": f"下游依赖: {dep.get('service', dep.get('name', ''))}",
                })
            evidence.append(f"发现 {len(downstream_deps)} 个下游依赖")

        # confidence 计算
        anomaly_count = sum(1 for f in findings if f.get("severity") == "high")
        confidence = min(0.9, 0.3 + 0.2 * anomaly_count) if findings else 0.1

        return SubAgentResult(
            agent_name="downstream_analyzer",
            dimension="downstream",
            findings=findings,
            confidence=confidence,
            evidence=evidence,
            timestamp=timestamp_str,
        )
    except Exception as e:
        return SubAgentResult(
            agent_name="downstream_analyzer",
            dimension="downstream",
            confidence=0.0,
            error=str(e),
            timestamp=timestamp_str,
        )
