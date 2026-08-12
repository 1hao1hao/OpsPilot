"""LangGraph 状态定义和主编排图骨架。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：DeepRCAState + StateGraph 骨架</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3.1, §3.4.1</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

__all__ = ["DeepRCAState", "TaskPlan"]


class TaskPlan(TypedDict):
    """单个分析任务计划。"""

    dimension: str           # change / upstream / downstream / cluster / errorlog / problem
    tool_name: str           # 对应的 L1 维度分析工具名
    params: dict             # 工具调用参数
    timeout: int             # 超时阈值（秒）
    priority: int            # 优先级（数字越小优先级越高）


class DeepRCAState(TypedDict):
    """LangGraph 主编排图共享状态。

    所有 L1 节点通过此 TypedDict 传递和更新状态。
    sub_agent_results 使用 operator.add reducer 合并并发结果。
    """

    # 输入
    alert: dict                          # ParsedAlert (序列化)
    # 任务计划
    task_plan: list[TaskPlan]
    # 并发分析结果（reducer 合并）
    sub_agent_results: Annotated[list[dict], operator.add]
    # 证据池摘要
    collected_evidence: dict | None      # EvidencePool summary
    # 根因结果
    root_cause: dict | None              # RootCauseResult (序列化)
    # 分析报告
    report: str | None                   # JSON string
    # 消息历史
    messages: Annotated[list, operator.add]
    # 元数据
    trace_id: str
    start_time: str
    status: str                          # running / completed / failed / timeout
    # 扩展字段
    related_services: list[str]          # 关联服务列表（intake 提取）
    degraded_mode: bool                  # 是否降级模式（dispatcher 全部失败）
