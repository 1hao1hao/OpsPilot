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
            for trace in traces:
                # 检测慢调用和错误调用
                duration = trace.get("duration_ms", trace.get("duration", 0))
                # trace 顶层没有 status，从 spans 中聚合
                spans = trace.get("spans", [])
                span_status = "error" if any(
                    s.get("status", "").upper() in ("ERROR", "FAILED", "TIMEOUT")
                    for s in spans
                ) else "success"
                span_name = trace.get("trace_id", "")
                if spans:
                    # 取第一个非本服务的 span 作为下游调用标识
                    for s in spans:
                        if s.get("service", "") != service_name:
                            span_name = s.get("service", span_name)
                            break

                if isinstance(duration, (int, float)) and duration > 1000:  # 超过 1 秒
                    findings.append({
                        "type": "slow_downstream_call",
                        "service": span_name,
                        "desc": f"下游调用超时: {span_name} 耗时 {duration}ms",
                        "severity": "high",
                    })
                    evidence.append(f"下游调用 {span_name} 耗时 {duration}ms")

                if span_status.upper() in ("ERROR", "FAILED", "TIMEOUT"):
                    findings.append({
                        "type": "downstream_call_error",
                        "service": span_name,
                        "desc": f"下游调用错误: {span_name} 状态 {span_status}",
                        "severity": "high",
                    })
                    evidence.append(f"下游调用 {span_name} 状态异常: {span_status}")

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
