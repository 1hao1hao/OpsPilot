"""错误日志查询工具 — query_error_logs。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3</td></tr>
<tr><td>0.2.0</td><td>Mock 模式改为 HTTP 调用 Mock API 获取场景感知数据</td><td>reviewer-fix-3</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

import re as _re
from collections import Counter

import httpx
from langchain_core.tools import tool

from deeprca.config import get_settings


def _extract_error_patterns(logs: list[dict]) -> list[dict]:
    """从日志中提取错误模式统计。"""
    messages = [log.get("message", "") for log in logs]
    patterns = Counter()
    for msg in messages:
        for match in _re.findall(r"([A-Z][a-z]+(?:\s+[a-z]+){1,3})", msg):
            patterns[match] += 1
    return [
        {"pattern": p, "count": c, "first_seen": logs[0].get("timestamp", "") if logs else ""}
        for p, c in patterns.most_common(5)
    ]


@tool
async def query_error_logs(
    service_name: str,
    start_time: str,
    end_time: str,
    level: str = "ERROR",
    keyword: str = "",
    limit: int = 100,
) -> dict:
    """查询错误日志。

    Args:
        service_name: 服务名称
        start_time: 起始时间 ISO 8601
        end_time: 结束时间 ISO 8601
        level: 日志级别
        keyword: 关键词过滤
        limit: 返回条数上限

    Returns:
        包含日志条目的字典
    """
    settings = get_settings()

    # Mock 模式：通过 Mock API 获取场景感知数据
    if settings.mock_env_enabled:
        try:
            async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                resp = await client.get(
                    f"{settings.mock_log_api}/api/v1/mock/service/{service_name}/logs",
                    params={"start_time": start_time, "end_time": end_time, "level": level, "keyword": keyword, "limit": limit},
                )
                resp.raise_for_status()
                data = resp.json()
                logs = data.get("logs", [])
                return {
                    "service": service_name,
                    "total": len(logs),
                    "logs": logs,
                    "error_patterns": _extract_error_patterns(logs),
                }
        except Exception as e:
            return {"service": service_name, "total": 0, "logs": [], "error_patterns": [], "error": str(e)}

    # 非 Mock 模式（占位实现）
    params: dict = {
        "service_name": service_name,
        "start_time": start_time,
        "end_time": end_time,
        "level": level,
        "limit": limit,
    }
    if keyword:
        params["keyword"] = keyword

    try:
        async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
            resp = await client.get(
                f"{settings.mock_log_api}/api/v1/logs",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            logs = data.get("logs", [])
            return {
                "service": service_name,
                "total": len(logs),
                "logs": logs,
                "error_patterns": _extract_error_patterns(logs),
            }
    except Exception as e:
        return {"service": service_name, "total": 0, "logs": [], "error_patterns": [], "error": str(e)}
