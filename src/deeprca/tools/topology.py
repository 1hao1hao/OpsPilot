"""服务拓扑查询工具 — query_topology。

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
async def query_topology(
    service_name: str,
    depth: int = 2,
    direction: str = "both",
) -> dict:
    """查询服务拓扑关系。

    Args:
        service_name: 服务名称
        depth: 拓扑深度，默认 2 层
        direction: 方向 (upstream/downstream/both)

    Returns:
        包含上下游依赖关系的字典
    """
    settings = get_settings()

    # Mock 模式：通过 Mock API 获取场景感知数据
    if settings.mock_env_enabled:
        try:
            async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                resp = await client.get(
                    f"{settings.mock_monitor_api}/api/v1/mock/service/{service_name}/topology",
                    params={"depth": depth},
                )
                resp.raise_for_status()
                data = resp.json()
                upstream = data.get("upstream", []) if direction in ("upstream", "both") else []
                downstream = data.get("downstream", []) if direction in ("downstream", "both") else []
                return {
                    "service": service_name,
                    "upstream": upstream,
                    "downstream": downstream,
                }
        except Exception as e:
            return {"service": service_name, "upstream": [], "downstream": [], "error": str(e)}

    # 非 Mock 模式（占位实现）
    params: dict = {
        "service_name": service_name,
        "depth": depth,
        "direction": direction,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
            resp = await client.get(
                f"{settings.mock_monitor_api}/api/v1/topology",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            upstream = data.get("upstream", []) if direction in ("upstream", "both") else []
            downstream = data.get("downstream", []) if direction in ("downstream", "both") else []
            return {
                "service": service_name,
                "upstream": upstream,
                "downstream": downstream,
            }
    except Exception as e:
        return {"service": service_name, "upstream": [], "downstream": [], "error": str(e)}
