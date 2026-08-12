"""调用链路查询工具 — query_trace。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3</td></tr>
<tr><td>0.2.0</td><td>Mock 模式改为 HTTP 调用 Mock API 获取场景感知数据</td><td>reviewer-fix-3</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

import httpx
from langchain_core.tools import tool

from deeprca.config import get_settings


def _extract_slow_spans(traces: list[dict]) -> list[dict]:
    """从 traces 中提取慢 span 统计。"""
    slow_spans: list[dict] = []
    span_durations: dict[str, list[float]] = {}
    for trace in traces:
        for span in trace.get("spans", []):
            svc = span.get("service", "unknown")
            dur = span.get("duration_ms", 0)
            span_durations.setdefault(svc, []).append(dur)
    for svc, durations in span_durations.items():
        if durations:
            slow_spans.append({
                "service": svc,
                "avg_duration_ms": sum(durations) / len(durations),
                "p99_duration_ms": sorted(durations)[int(len(durations) * 0.99)] if len(durations) > 1 else durations[0],
            })
    slow_spans.sort(key=lambda s: s["avg_duration_ms"], reverse=True)
    return slow_spans


@tool
async def query_trace(
    service_name: str,
    start_time: str,
    end_time: str,
    trace_id: str = "",
    status: str = "",
    limit: int = 50,
) -> dict:
    """查询调用链路数据。

    Args:
        service_name: 服务名称
        start_time: 起始时间 ISO 8601
        end_time: 结束时间 ISO 8601
        trace_id: 指定 trace ID（可选）
        status: 链路状态过滤 (success/error/timeout)
        limit: 返回条数上限

    Returns:
        包含调用链数据和慢 span 统计的字典
    """
    settings = get_settings()

    # Mock 模式：通过 Mock API 获取场景感知数据
    if settings.mock_env_enabled:
        try:
            async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                resp = await client.get(
                    f"{settings.mock_monitor_api}/api/v1/mock/service/{service_name}/traces",
                    params={"start_time": start_time, "end_time": end_time, "limit": limit},
                )
                resp.raise_for_status()
                data = resp.json()
                traces = data.get("traces", [])
                return {
                    "service": service_name,
                    "total": len(traces),
                    "traces": traces,
                    "slow_spans": _extract_slow_spans(traces)[:10],
                }
        except Exception as e:
            return {"service": service_name, "total": 0, "traces": [], "slow_spans": [], "error": str(e)}

    # 非 Mock 模式（占位实现）
    params: dict = {
        "service_name": service_name,
        "start_time": start_time,
        "end_time": end_time,
        "limit": limit,
    }
    if trace_id:
        params["trace_id"] = trace_id
    if status:
        params["status"] = status

    try:
        async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
            resp = await client.get(
                f"{settings.mock_monitor_api}/api/v1/traces",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            traces = data.get("traces", [])
            return {
                "service": service_name,
                "total": len(traces),
                "traces": traces,
                "slow_spans": _extract_slow_spans(traces)[:10],
            }
    except Exception as e:
        return {"service": service_name, "total": 0, "traces": [], "slow_spans": [], "error": str(e)}
