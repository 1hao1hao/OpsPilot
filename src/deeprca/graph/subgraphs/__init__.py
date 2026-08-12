"""子图模块 — L2 领域专家子 Agent。

包含 4 个领域专家子 Agent（DB/Redis/Mafka/RPC），各自构建
collect → analyze → conclude 三节点 LangGraph 子图。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：子图模块骨架</td><td>REQ: 20260713-总体架构</td></tr>
<tr><td>0.3.0</td><td>实现 4 个领域专家 + 注册表 + 并发调度</td><td>REQ: PRD-03 领域专家子 Agent</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from deeprca.graph.subgraphs.base_expert import BaseExpertAgent
from deeprca.graph.subgraphs.db_expert import DBExpertAgent
from deeprca.graph.subgraphs.redis_expert import RedisExpertAgent
from deeprca.graph.subgraphs.mafka_expert import MafkaExpertAgent
from deeprca.graph.subgraphs.rpc_expert import RPCExpertAgent
from deeprca.graph.subgraphs.registry import (
    EXPERT_AGENT_REGISTRY,
    dispatch_to_experts,
    get_expert_agent,
)

__all__ = [
    "BaseExpertAgent",
    "DBExpertAgent",
    "RedisExpertAgent",
    "MafkaExpertAgent",
    "RPCExpertAgent",
    "EXPERT_AGENT_REGISTRY",
    "get_expert_agent",
    "dispatch_to_experts",
]
