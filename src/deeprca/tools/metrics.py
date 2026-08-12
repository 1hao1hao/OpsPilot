"""监控指标查询工具 — query_metrics。

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


@tool
async def query_metrics(
    service_name: str,
    metric_name: str,
    start_time: str,
    end_time: str,
    granularity: str = "1m",
    labels: dict | None = None,
) -> dict:
    """查询监控指标时序数据。

    Args:
        service_name: 服务名称
        metric_name: 指标名称（如 qps, error_rate, tp99）
        start_time: 起始时间 ISO 8601
        end_time: 结束时间 ISO 8601
        granularity: 粒度 (1m/5m/1h)
        labels: 标签过滤条件

    Returns:
        包含时序数据点和聚合统计的字典
    """
    settings = get_settings()

    # Mock 模式：通过 Mock API 获取场景感知数据
    if settings.mock_env_enabled:
        try:
            async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                resp = await client.get(
                    f"{settings.mock_monitor_api}/api/v1/mock/service/{service_name}/metrics/{metric_name}",
                    params={"start_time": start_time, "end_time": end_time},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            return {"service": service_name, "metric": metric_name, "data_points": [], "aggregation": {}, "error": str(e)}

    # 非 Mock 模式（占位实现，需对接真实监控系统时重新设计）
    params: dict = {
        "service_name": service_name,
        "metric_name": metric_name,
        "start_time": start_time,
        "end_time": end_time,
        "granularity": granularity,
    }
    if labels:
        params["labels"] = labels

    try:
        async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
            resp = await client.get(
                f"{settings.mock_monitor_api}/api/v1/metrics",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            data_points = data.get("data_points", [])
            # 计算聚合统计
            values = [p.get("value", 0) for p in data_points]
            aggregation = {}
            if values:
                aggregation = {
                    "min": min(values),
                    "max": max(values),
                    "avg": sum(values) / len(values),
                }
            return {
                "service": service_name,
                "metric": metric_name,
                "data_points": data_points,
                "aggregation": aggregation,
            }
    except Exception as e:
        return {"service": service_name, "metric": metric_name, "data_points": [], "aggregation": {}, "error": str(e)}
