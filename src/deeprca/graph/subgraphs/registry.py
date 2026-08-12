"""L2 领域专家注册表与并发调度。

PRD-03 §2.2 子 Agent 注册与调度
PRD-03 §9   并发调度集成

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.3.0</td><td>初始创建：注册表 + dispatch_to_experts</td><td>REQ: PRD-03 §2.2, §9</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

import asyncio
import time

from deeprca.graph.subgraphs.base_expert import BaseExpertAgent
from deeprca.graph.subgraphs.db_expert import DBExpertAgent
from deeprca.graph.subgraphs.redis_expert import RedisExpertAgent
from deeprca.graph.subgraphs.mafka_expert import MafkaExpertAgent
from deeprca.graph.subgraphs.rpc_expert import RPCExpertAgent
from deeprca.models import SubAgentResult

__all__ = [
    "EXPERT_AGENT_REGISTRY",
    "get_expert_agent",
    "dispatch_to_experts",
]


# ─────────────────────────────────────────────
# §2.2 注册表
# ─────────────────────────────────────────────
EXPERT_AGENT_REGISTRY: dict[str, type[BaseExpertAgent]] = {
    "db": DBExpertAgent,
    "redis": RedisExpertAgent,
    "mafka": MafkaExpertAgent,
    "rpc": RPCExpertAgent,
}

# 维度 → 领域专家映射（PRD-03 §9 domain_map）
# downstream 维度可触发多个领域专家
_DIMENSION_DOMAIN_MAP: dict[str, str | list[str] | None] = {
    "change": None,       # 变更分析由 L1 维度 Agent 处理，不需要 L2 专家
    "upstream": "rpc",    # 上游调用复用 RPC 专家
    "downstream": ["db", "redis", "mafka", "rpc"],  # 下游拆分为多个领域
    "cluster": "rpc",     # 集群状态复用 RPC 工具
    "errorlog": None,     # 错误日志由 L1 维度 Agent 处理
    "problem": None,      # 已知问题匹配不需要子 Agent
}


def get_expert_agent(domain: str) -> BaseExpertAgent:
    """根据领域获取对应的专家 Agent 实例。

    Args:
        domain: 领域标识 (db / redis / mafka / rpc)

    Returns:
        对应的专家 Agent 实例

    Raises:
        ValueError: 未知的领域标识
    """
    agent_cls = EXPERT_AGENT_REGISTRY.get(domain)
    if agent_cls is None:
        raise ValueError(f"未知的领域专家: {domain}")
    return agent_cls()


# ─────────────────────────────────────────────
# §9 并发调度
# ─────────────────────────────────────────────
async def dispatch_to_experts(
    task_plan: list[dict],
    alert: dict,
    context: dict | None = None,
    timeout: int | None = None,
) -> list[SubAgentResult]:
    """并发调度所有领域专家子 Agent。

    根据任务计划中的维度，映射到对应的 L2 领域专家并并发执行。
    单个专家失败不阻塞其他专家。

    Args:
        task_plan: L1 规划的任务列表，每项含 dimension 字段
        alert: 告警信息字典
        context: L1 分析上下文（含已发现的异常线索），默认空字典
        timeout: 单个专家执行超时（秒），默认使用 settings.tool_call_timeout

    Returns:
        所有专家的 SubAgentResult 列表
    """
    if context is None:
        context = {}
    if timeout is None:
        from deeprca.config import get_settings
        timeout = get_settings().tool_call_timeout

    # 收集需要触发的领域专家
    domains_to_run: list[str] = []
    for task in task_plan:
        dimension = task.get("dimension", "")
        domains = _DIMENSION_DOMAIN_MAP.get(dimension)
        if domains is None:
            continue
        if isinstance(domains, str):
            domains = [domains]
        for domain in domains:
            if domain not in domains_to_run:
                domains_to_run.append(domain)

    if not domains_to_run:
        return []

    # 并发执行所有领域专家
    tasks = [
        _run_expert(domain, alert, context, timeout)
        for domain in domains_to_run
    ]
    results = await asyncio.gather(*tasks)
    return list(results)


async def _run_expert(
    domain: str,
    alert: dict,
    context: dict,
    timeout: int,
) -> SubAgentResult:
    """执行单个领域专家，带超时和错误降级。"""
    try:
        agent = get_expert_agent(domain)
        result = await asyncio.wait_for(
            agent.analyze(alert, context),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        return SubAgentResult(
            agent_name=f"{domain}_expert",
            dimension=domain,
            confidence=0.0,
            error=f"领域专家执行超时 ({timeout}s)",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
    except Exception as e:
        return SubAgentResult(
            agent_name=f"{domain}_expert",
            dimension=domain,
            confidence=0.0,
            error=f"领域专家执行失败: {e!s}",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
