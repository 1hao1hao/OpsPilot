"""LangGraph 主编排图 — 使用真实节点函数构建。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：build_coordinator_graph 骨架（6节点+条件边）</td><td>REQ: 20260713-总体架构, TECH: 04b §3.4.1</td></tr>
<tr><td>0.1.1</td><td>替换占位符为真实节点函数</td><td>REQ: 20260713-总体架构, TECH: 04b §3.4.1</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from deeprca.agents.coordinator import (
    check_timeout,
    collector_node,
    dispatcher_node,
    intake_node,
    planner_node,
    reporter_node,
    root_cause_node,
)
from deeprca.graph.state import DeepRCAState

__all__ = ["build_coordinator_graph"]


def build_coordinator_graph():
    """构建 L1 通用分析 Agent 主编排图。

    节点流转:
        intake → planner → dispatcher → [normal: collector | timeout: root_cause]
        collector → root_cause → reporter → END

    Returns:
        CompiledStateGraph: 编译后的 LangGraph 图
    """
    graph: StateGraph = StateGraph(DeepRCAState)

    # 添加 6 个真实节点
    graph.add_node("intake", intake_node)
    graph.add_node("planner", planner_node)
    graph.add_node("dispatcher", dispatcher_node)
    graph.add_node("collector", collector_node)
    # LangGraph 0.1.19+ 不允许节点名与 state key 同名。
    graph.add_node("root_cause_agent", root_cause_node)
    graph.add_node("reporter", reporter_node)

    # 设置入口
    graph.set_entry_point("intake")

    # 线性边
    graph.add_edge("intake", "planner")
    graph.add_edge("planner", "dispatcher")

    # 条件边：超时降级
    graph.add_conditional_edges(
        "dispatcher",
        check_timeout,
        {
            "normal": "collector",
            "timeout": "root_cause_agent",
        },
    )

    # 汇聚到根因定位
    graph.add_edge("collector", "root_cause_agent")
    graph.add_edge("root_cause_agent", "reporter")
    graph.add_edge("reporter", END)

    return graph.compile()
