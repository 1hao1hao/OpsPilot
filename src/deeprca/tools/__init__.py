"""LangChain @tool 工具集 — 6 个查询工具接口定义。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：6 个 @tool 工具签名（骨架实现）</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3, §2.2</td></tr>
<tr><td>0.1.1</td><td>拆分到独立模块，实现具体逻辑</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from deeprca.tools.alerts import query_related_alerts
from deeprca.tools.changes import query_recent_changes
from deeprca.tools.logs import query_error_logs
from deeprca.tools.metrics import query_metrics
from deeprca.tools.topology import query_topology
from deeprca.tools.traces import query_trace

__all__ = [
    "query_metrics",
    "query_error_logs",
    "query_recent_changes",
    "query_trace",
    "query_related_alerts",
    "query_topology",
]
